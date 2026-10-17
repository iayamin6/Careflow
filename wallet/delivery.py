"""Durable outbox with opt-in allowlisted delivery. SMTP acceptance is not delivery."""

import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
import httpx
from .store import connect, utc, audit


def enqueue(c, key, action):
    p = c.execute(
        "SELECT * FROM preferences WHERE customer_id=?", (action["entity_id"],)
    ).fetchone()
    channel = p["channel"] if p else "in_app"
    c.execute(
        "INSERT OR IGNORE INTO deliveries(execution_key,channel,destination,status,updated_at) VALUES(?,?,?,?,?)",
        (key, channel, p["destination"] if p else None, "queued", utc()),
    )


def send(channel, destination, draft, key):
    # Explicit allowlist is required even after a human verifies a destination.
    allowed = {
        x.strip()
        for x in os.getenv("CAREFLOW_DELIVERY_ALLOWLIST", "").split(",")
        if x.strip()
    }
    if destination not in allowed:
        raise ValueError("Destination is not in the configured delivery allowlist")
    if channel == "email":
        host = os.environ["CAREFLOW_SMTP_HOST"]
        port = int(os.getenv("CAREFLOW_SMTP_PORT", "465"))
        msg = EmailMessage()
        msg["From"] = os.environ["CAREFLOW_SMTP_FROM"]
        msg["To"] = destination
        msg["Subject"] = draft["subject"]
        msg["Message-ID"] = f"<{key}@careflow.local>"
        msg.set_content(draft["body"])
        with smtplib.SMTP_SSL(
            host, port, context=ssl.create_default_context(), timeout=15
        ) as smtp:
            if os.getenv("CAREFLOW_SMTP_USER"):
                smtp.login(
                    os.environ["CAREFLOW_SMTP_USER"],
                    os.environ["CAREFLOW_SMTP_PASSWORD"],
                )
            smtp.send_message(msg)
        return "accepted", str(msg["Message-ID"])
    if channel == "webhook":
        url = os.environ["CAREFLOW_DELIVERY_WEBHOOK"]
        if not url.startswith("https://"):
            raise ValueError("Delivery webhook requires HTTPS")
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            r = client.post(
                url,
                json={"destination": destination, "message": draft},
                headers={
                    "Idempotency-Key": key,
                    "Authorization": "Bearer " + os.environ["CAREFLOW_DELIVERY_TOKEN"],
                },
            )
            r.raise_for_status()
            return "accepted", str(r.json().get("id", key))
    raise ValueError("Unsupported external delivery channel")


def dispatch(limit=20):
    results = []
    from .operations import communication_allowed
    from .analytics import snapshot

    for _ in range(min(limit, 100)):
        with connect() as c:
            version = c.execute(
                "SELECT COALESCE(MAX(rowid),0) FROM payment_events"
            ).fetchone()[0]
        data = snapshot()
        with connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if (
                c.execute(
                    "SELECT COALESCE(MAX(rowid),0) FROM payment_events"
                ).fetchone()[0]
                != version
            ):
                raise ValueError(
                    "Payment evidence changed; retry dispatch with fresh evidence"
                )
            r = c.execute(
                "SELECT * FROM deliveries WHERE status IN ('queued','retry') AND (next_retry_at IS NULL OR next_retry_at<=?) ORDER BY id LIMIT 1",
                (utc(),),
            ).fetchone()
            if not r:
                break
            r = dict(r)
            a = c.execute(
                "SELECT a.* FROM actions a JOIN executions e ON e.action_id=a.id WHERE e.execution_key=?",
                (r["execution_key"],),
            ).fetchone()
            customer = c.execute(
                "SELECT * FROM wallet_customers WHERE id=?", (a["entity_id"],)
            ).fetchone()
            p = c.execute(
                "SELECT * FROM preferences WHERE customer_id=?", (a["entity_id"],)
            ).fetchone()
            current = next(
                (x for x in data["customers"] if x["id"] == a["entity_id"]), None
            )
            try:
                communication_allowed(c, a)
                if a["kind"] == "resolution_follow_up":
                    from .operations import resolution_allowed

                    resolution_allowed(c, a, current)
                if (
                    not customer["consent"]
                    or not current
                    or current["unresolved_transactions"]
                ):
                    raise ValueError("Consent or payment eligibility changed")
                if any(
                    a["entity_id"] in m["affected_customers"]
                    for m in data["merchants"]
                    if m["operational_problem"] or m["reliability_deterioration"]
                ):
                    raise ValueError("Merchant incident blocks delivery")
                if p and (
                    p["channel"] != r["channel"] or p["destination"] != r["destination"]
                ):
                    raise ValueError("Destination changed after action execution")
                live = (
                    os.getenv("CAREFLOW_DELIVERY_MODE", "dry_run") == "live"
                    and r["channel"] != "in_app"
                )
                if live and (not p or not p["verified"]):
                    raise ValueError("Recipient verification required")
                if not live:
                    c.execute(
                        "UPDATE deliveries SET status='simulated',updated_at=? WHERE id=?",
                        (utc(), r["id"]),
                    )
                    audit(c, "delivery_simulated", r["id"], {})
                    results.append({"id": r["id"], "status": "simulated"})
                    continue
            except ValueError as e:
                c.execute(
                    "UPDATE deliveries SET status='blocked',last_error=?,updated_at=? WHERE id=?",
                    (str(e), utc(), r["id"]),
                )
                audit(c, "delivery_blocked", r["id"], {"reason": str(e)})
                results.append({"id": r["id"], "status": "blocked"})
                continue
            # A crash after claim leaves 'sending': reconcile manually, never blindly resend.
            c.execute(
                "UPDATE deliveries SET status='sending',attempts=attempts+1,updated_at=? WHERE id=?",
                (utc(), r["id"]),
            )
            detail = json.loads(
                c.execute(
                    "SELECT detail FROM executions WHERE execution_key=?",
                    (r["execution_key"],),
                ).fetchone()[0]
            )
        try:
            status, provider = send(
                r["channel"], r["destination"], detail["draft"], r["execution_key"]
            )
            error = None
            retry = None
        except (ValueError, KeyError) as e:
            status = "blocked"
            provider = None
            error = str(e)
            retry = None
        except (httpx.ConnectError, smtplib.SMTPConnectError) as e:
            # Only failures known to precede acceptance are automatically retried.
            status = "failed" if r["attempts"] + 1 >= 3 else "retry"
            provider = None
            error = type(e).__name__
            retry = (
                utc(
                    (
                        datetime.now(timezone.utc)
                        + timedelta(seconds=30 * 2 ** r["attempts"])
                    ).isoformat()
                )
                if status == "retry"
                else None
            )
        except Exception as e:
            status = "unknown"
            provider = None
            error = type(e).__name__
            retry = None
        with connect() as c:
            c.execute(
                "UPDATE deliveries SET status=?,provider_id=?,last_error=?,next_retry_at=?,updated_at=? WHERE id=?",
                (status, provider, error, retry, utc(), r["id"]),
            )
            audit(c, "delivery_" + status, r["id"], {"error": error})
        results.append({"id": r["id"], "status": status})
    return results


def receipt(id, delivery_id, status):
    if status not in ("delivered", "bounced"):
        raise ValueError("Receipt must confirm delivered or bounced")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        old = c.execute("SELECT * FROM delivery_receipts WHERE id=?", (id,)).fetchone()
        if old:
            if old["delivery_id"] != delivery_id or old["status"] != status:
                raise ValueError("Conflicting receipt ID")
            return {"duplicate": True}
        d = c.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
        if not d or d["status"] not in ("accepted", "unknown", "sending", status):
            raise ValueError("Delivery is not awaiting a receipt")
        c.execute(
            "INSERT INTO delivery_receipts VALUES(?,?,?,?)",
            (id, delivery_id, status, utc()),
        )
        c.execute(
            "UPDATE deliveries SET status=?,updated_at=? WHERE id=?",
            (status, utc(), delivery_id),
        )
        audit(c, "delivery_receipt", delivery_id, {"receipt_id": id, "status": status})
    return {"duplicate": False, "status": status}
