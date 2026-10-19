"""Additive migrations and an immutable event log; money is in minor units."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from . import config


def utc(value=None):
    d = (
        datetime.now(timezone.utc)
        if value is None
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if d.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return d.astimezone(timezone.utc).isoformat(timespec="microseconds")


@contextmanager
def connect():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS wallet_customers(
          id TEXT PRIMARY KEY, joined_at TEXT NOT NULL, consent INTEGER NOT NULL DEFAULT 0,
          synthetic INTEGER NOT NULL DEFAULT 1, legacy_customer_id TEXT);
        CREATE TABLE IF NOT EXISTS merchants(
          id TEXT PRIMARY KEY, category TEXT NOT NULL, joined_at TEXT NOT NULL, size TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS transactions(
          id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES wallet_customers(id),
          merchant_id TEXT NOT NULL REFERENCES merchants(id), amount_minor INTEGER NOT NULL CHECK(amount_minor>0),
          currency TEXT NOT NULL CHECK(currency='BDT'), channel TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payment_events(
          event_id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL REFERENCES transactions(id),
          attempt_id TEXT NOT NULL, status TEXT NOT NULL,
          occurred_at TEXT NOT NULL, received_at TEXT NOT NULL, latency_ms INTEGER,
          failure_category TEXT, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS events_time ON payment_events(occurred_at,received_at);
        CREATE INDEX IF NOT EXISTS events_transaction ON payment_events(transaction_id,attempt_id,occurred_at);
        CREATE TABLE IF NOT EXISTS actions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
          kind TEXT NOT NULL, priority TEXT NOT NULL, evidence TEXT NOT NULL,
          status TEXT NOT NULL, approval_required INTEGER NOT NULL, approved INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
          next_retry_at TEXT, last_error TEXT, policy_version TEXT NOT NULL DEFAULT 'wallet-v1');
        CREATE UNIQUE INDEX IF NOT EXISTS one_open_action ON actions(entity_type,entity_id,kind)
          WHERE status IN ('pending','approved','retry','executing');
        CREATE TABLE IF NOT EXISTS executions(
          execution_key TEXT PRIMARY KEY, action_id INTEGER NOT NULL REFERENCES actions(id),
          status TEXT NOT NULL, transport TEXT NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(
          id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, entity_id TEXT NOT NULL,
          detail TEXT NOT NULL, created_at TEXT NOT NULL);
        """)
        c.execute("INSERT OR IGNORE INTO schema_versions VALUES(1,?)", (utc(),))
        from .migrations import upgrade

        upgrade(c, utc())


def audit(c, kind, entity_id, detail):
    from .security import actor

    detail = {**detail, "actor": actor.get()}
    c.execute(
        "INSERT INTO audit(kind,entity_id,detail,created_at) VALUES(?,?,?,?)",
        (kind, str(entity_id), json.dumps(detail, sort_keys=True), utc()),
    )


def put_customer(data):
    with connect() as c:
        c.execute(
            """INSERT INTO wallet_customers VALUES(:id,:joined_at,:consent,:synthetic,:legacy_customer_id)
          ON CONFLICT(id) DO UPDATE SET consent=excluded.consent""",
            data,
        )
        audit(c, "customer_updated", data["id"], {"consent": data["consent"]})


def put_merchant(data):
    with connect() as c:
        c.execute(
            "INSERT INTO merchants VALUES(:id,:category,:joined_at,:size) ON CONFLICT(id) DO NOTHING",
            data,
        )


def ingest(data):
    data = dict(data)
    data["occurred_at"] = utc(data["occurred_at"])
    # Receipt time is server-owned; clients cannot backdate visibility.
    data["received_at"] = utc()
    payload = json.dumps(
        {k: v for k, v in data.items() if k != "received_at"}, sort_keys=True
    )
    with connect() as c:
        existing = c.execute(
            "SELECT payload FROM payment_events WHERE event_id=?", (data["event_id"],)
        ).fetchone()
        if existing:
            if existing["payload"] != payload:
                raise ValueError("Event ID already exists with a different payload")
            return {"duplicate": True, "event_id": data["event_id"]}
        customer = c.execute(
            "SELECT joined_at FROM wallet_customers WHERE id=?", (data["customer_id"],)
        ).fetchone()
        merchant = c.execute(
            "SELECT joined_at FROM merchants WHERE id=?", (data["merchant_id"],)
        ).fetchone()
        if not customer or not merchant:
            raise ValueError(
                "Customer and merchant must exist before payment ingestion"
            )
        if data["occurred_at"] < max(customer["joined_at"], merchant["joined_at"]):
            raise ValueError("Payment cannot precede onboarding")
        if data["occurred_at"] > data["received_at"]:
            raise ValueError("Future payment events are not accepted")
        if data["status"] in ("refunded", "reversed"):
            succeeded = c.execute(
                "SELECT 1 FROM payment_events WHERE transaction_id=? AND status='succeeded' AND occurred_at<=?",
                (data["transaction_id"], data["occurred_at"]),
            ).fetchone()
            if not succeeded:
                raise ValueError(
                    "Refund/reversal requires an earlier successful original payment"
                )
        identity = (
            data["customer_id"],
            data["merchant_id"],
            data["amount_minor"],
            data["currency"],
            data["channel"],
        )
        old = c.execute(
            "SELECT * FROM transactions WHERE id=?", (data["transaction_id"],)
        ).fetchone()
        if (
            old
            and tuple(
                old[k]
                for k in (
                    "customer_id",
                    "merchant_id",
                    "amount_minor",
                    "currency",
                    "channel",
                )
            )
            != identity
        ):
            raise ValueError("Immutable transaction fields do not match")
        attempt = c.execute(
            "SELECT transaction_id FROM payment_events WHERE attempt_id=? LIMIT 1",
            (data["attempt_id"],),
        ).fetchone()
        if attempt and attempt[0] != data["transaction_id"]:
            raise ValueError("Attempt ID belongs to another transaction")
        c.execute(
            "INSERT OR IGNORE INTO transactions VALUES(?,?,?,?,?,?,?)",
            (data["transaction_id"], *identity, data["occurred_at"]),
        )
        c.execute(
            "UPDATE transactions SET created_at=MIN(created_at,?) WHERE id=?",
            (data["occurred_at"], data["transaction_id"]),
        )
        c.execute(
            "INSERT INTO payment_events VALUES(?,?,?,?,?,?,?,?,?)",
            (
                data["event_id"],
                data["transaction_id"],
                data["attempt_id"],
                data["status"],
                data["occurred_at"],
                data["received_at"],
                data.get("latency_ms"),
                data.get("failure_category"),
                payload,
            ),
        )
        audit(
            c,
            "payment_event",
            data["event_id"],
            {"transaction_id": data["transaction_id"], "status": data["status"]},
        )
    return {"duplicate": False, "event_id": data["event_id"]}
