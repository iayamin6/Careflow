"""Incident lifecycle, preferences, payment servicing and evidence-based drafts."""

import json
from datetime import datetime, timedelta, timezone
from .store import connect, utc, audit
from .security import actor


def sync_incidents():
    with connect() as c:
        for a in c.execute(
            "SELECT * FROM actions WHERE kind IN ('payment_investigation','merchant_investigation','payment_risk_review') AND status NOT IN ('superseded','dismissed')"
        ).fetchall():
            c.execute(
                "INSERT OR IGNORE INTO incidents(action_id,entity_type,entity_id,priority,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (
                    a["id"],
                    a["entity_type"],
                    a["entity_id"],
                    a["priority"],
                    a["created_at"],
                    utc(),
                ),
            )


def incidents():
    sync_incidents()
    with connect() as c:
        rows = [dict(x) for x in c.execute("SELECT * FROM incidents ORDER BY id DESC")]
        for r in rows:
            r["notes"] = [
                dict(x)
                for x in c.execute(
                    "SELECT * FROM incident_notes WHERE incident_id=? ORDER BY id",
                    (r["id"],),
                )
            ]
    return rows


def update_incident(id, status, owner, note):
    if (
        status not in ("open", "investigating", "escalated", "resolved")
        or not note.strip()
    ):
        raise ValueError("A valid incident status and reason are required")
    if status == "resolved":
        from .analytics import snapshot

        data = snapshot()
        with connect() as c:
            incident = c.execute("SELECT * FROM incidents WHERE id=?", (id,)).fetchone()
        if not incident:
            raise ValueError("Incident not found")
        rows = data[
            "customers" if incident["entity_type"] == "customer" else "merchants"
        ]
        entity = next((r for r in rows if r["id"] == incident["entity_id"]), None)
        if (
            not entity
            or entity["unresolved_transactions"]
            or entity.get("overdue_settlements", 0)
        ):
            raise ValueError("Unresolved payment evidence prevents incident closure")
    with connect() as c:
        cur = c.execute(
            "UPDATE incidents SET status=?,owner=?,resolution=?,updated_at=? WHERE id=?",
            (status, owner, note if status == "resolved" else None, utc(), id),
        )
        if not cur.rowcount:
            raise ValueError("Incident not found")
        c.execute(
            "INSERT INTO incident_notes(incident_id,author,note,created_at) VALUES(?,?,?,?)",
            (id, actor.get(), note, utc()),
        )
        audit(c, "incident_" + status, id, {"owner": owner, "note": note})
    return {"id": id, "status": status}


def override(id, reason, days=7):
    if not reason.strip() or not 1 <= days <= 30:
        raise ValueError("Reason and 1–30 day override duration required")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        a = c.execute("SELECT * FROM actions WHERE id=?", (id,)).fetchone()
        if not a or a["status"] not in ("pending", "approved", "retry"):
            raise ValueError("Only open actions can be dismissed")
        c.execute(
            "UPDATE actions SET status='dismissed',updated_at=? WHERE id=?", (utc(), id)
        )
        expires = utc((datetime.now(timezone.utc) + timedelta(days=days)).isoformat())
        c.execute(
            "INSERT INTO overrides(action_id,entity_type,entity_id,kind,reason,actor,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                id,
                a["entity_type"],
                a["entity_id"],
                a["kind"],
                reason,
                actor.get(),
                expires,
                utc(),
            ),
        )
        audit(c, "action_override", id, {"reason": reason, "expires_at": expires})
    return {"id": id, "status": "dismissed", "expires_at": expires}


def preferences(
    id,
    channel="in_app",
    support=True,
    marketing=False,
    destination=None,
    verified=False,
):
    if channel not in ("in_app", "email", "webhook"):
        raise ValueError("Unsupported communication channel")
    if (
        channel == "email"
        and destination
        and ("@" not in destination or "\n" in destination or "\r" in destination)
    ):
        raise ValueError("Invalid email destination")
    # No caller can self-verify recipients from an ordinary preferences update.
    with connect() as c:
        if not c.execute("SELECT 1 FROM wallet_customers WHERE id=?", (id,)).fetchone():
            raise ValueError("Customer not found")
        c.execute(
            "INSERT INTO preferences VALUES(?,?,?,?,?,?) ON CONFLICT(customer_id) DO UPDATE SET channel=excluded.channel,support=excluded.support,marketing=excluded.marketing,destination=excluded.destination,verified=0",
            (id, channel, support, marketing, destination, False),
        )
        audit(
            c,
            "preferences_updated",
            id,
            {"channel": channel, "support": support, "marketing": marketing},
        )
    return {"customer_id": id, "verified": False}


def verify_destination(id):
    # Admin-only endpoint; verified destinations must additionally be explicitly allowlisted at dispatch.
    with connect() as c:
        p = c.execute("SELECT * FROM preferences WHERE customer_id=?", (id,)).fetchone()
        if not p or not p["destination"]:
            raise ValueError("A destination must be supplied first")
        c.execute("UPDATE preferences SET verified=1 WHERE customer_id=?", (id,))
        audit(c, "destination_verified", id, {"channel": p["channel"]})
    return {"customer_id": id, "verified": True}


def communication_allowed(c, action):
    p = c.execute(
        "SELECT * FROM preferences WHERE customer_id=?", (action["entity_id"],)
    ).fetchone()
    marketing = action["kind"] in ("retention_outreach", "re_engagement")
    if p and not p["marketing" if marketing else "support"]:
        raise ValueError("Customer communication preference blocks this category")
    if (
        marketing
        and c.execute(
            "SELECT 1 FROM complaints WHERE customer_id=? AND status='open'",
            (action["entity_id"],),
        ).fetchone()
    ):
        raise ValueError("Open support complaint blocks retention outreach")
    if (
        marketing
        and c.execute(
            "SELECT 1 FROM assignments x JOIN experiments e ON e.id=x.experiment_id WHERE x.customer_id=? AND x.arm='control' AND e.status='active'",
            (action["entity_id"],),
        ).fetchone()
    ):
        raise ValueError("Active experiment holdout blocks retention outreach")


def settlement(data):
    data = dict(data)
    data["due_at"] = utc(data["due_at"])
    data["settled_at"] = utc(data["settled_at"]) if data.get("settled_at") else None
    with connect() as c:
        t = c.execute(
            "SELECT * FROM transactions WHERE id=?", (data["transaction_id"],)
        ).fetchone()
        if not t or data["amount_minor"] != t["amount_minor"]:
            raise ValueError("Settlement must match its original transaction amount")
        if not c.execute(
            "SELECT 1 FROM payment_events WHERE transaction_id=? AND status='succeeded'",
            (t["id"],),
        ).fetchone():
            raise ValueError("Settlement requires a confirmed successful payment")
        if data["due_at"] < t["created_at"] or (
            data["settled_at"]
            and (data["settled_at"] < t["created_at"] or data["settled_at"] > utc())
        ):
            raise ValueError("Invalid settlement timestamp")
        existing = c.execute(
            "SELECT * FROM settlements WHERE id=?", (data["id"],)
        ).fetchone()
        if existing and (
            existing["transaction_id"] != t["id"]
            or existing["amount_minor"] != data["amount_minor"]
            or existing["due_at"] != data["due_at"]
        ):
            raise ValueError("Conflicting settlement identity")
        if (
            existing
            and existing["settled_at"]
            and existing["settled_at"] != data["settled_at"]
        ):
            raise ValueError("Settled record is immutable")
        status = "settled" if data["settled_at"] else "pending"
        c.execute(
            "INSERT INTO settlements VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET settled_at=excluded.settled_at,status=excluded.status",
            (
                data["id"],
                t["id"],
                data["due_at"],
                data["settled_at"],
                data["amount_minor"],
                status,
            ),
        )
        audit(c, "settlement_recorded", data["id"], {"status": status})
    return {"id": data["id"], "status": status}


def refund(data):
    data = dict(data)
    if (
        data["status"] not in ("requested", "approved", "completed", "rejected")
        or data["amount_minor"] <= 0
    ):
        raise ValueError("Invalid refund request")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        t = c.execute(
            "SELECT * FROM transactions WHERE id=?", (data["transaction_id"],)
        ).fetchone()
        if (
            not t
            or not c.execute(
                "SELECT 1 FROM payment_events WHERE transaction_id=? AND status='succeeded'",
                (data["transaction_id"],),
            ).fetchone()
        ):
            raise ValueError("Refund requires an original successful payment")
        old = c.execute("SELECT * FROM refunds WHERE id=?", (data["id"],)).fetchone()
        if (
            not old
            and c.execute(
                "SELECT 1 FROM payment_events WHERE transaction_id=? AND status IN ('refunded','reversed')",
                (t["id"],),
            ).fetchone()
        ):
            raise ValueError("Original payment is already fully refunded or reversed")
        if old and (
            old["transaction_id"] != t["id"]
            or old["amount_minor"] != data["amount_minor"]
        ):
            raise ValueError("Conflicting refund identity")
        if (
            old
            and old["status"] in ("completed", "rejected")
            and old["status"] != data["status"]
        ):
            raise ValueError("Refund is already terminal")
        allowed = {
            "requested": {"requested", "approved", "rejected"},
            "approved": {"approved", "completed", "rejected"},
        }
        if (not old and data["status"] != "requested") or (
            old
            and old["status"] in allowed
            and data["status"] not in allowed[old["status"]]
        ):
            raise ValueError("Invalid refund lifecycle transition")
        total = c.execute(
            "SELECT COALESCE(SUM(amount_minor),0) FROM refunds WHERE transaction_id=? AND id!=? AND status!='rejected'",
            (t["id"], data["id"]),
        ).fetchone()[0]
        if total + data["amount_minor"] > t["amount_minor"]:
            raise ValueError("Cumulative refund exceeds original payment")
        c.execute(
            "INSERT INTO refunds VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,resolved_at=excluded.resolved_at",
            (
                data["id"],
                t["id"],
                data["amount_minor"],
                data["status"],
                old["requested_at"] if old else utc(),
                (old["resolved_at"] if old and old["resolved_at"] else utc())
                if data["status"] in ("completed", "rejected")
                else None,
            ),
        )
        completed = c.execute(
            "SELECT COALESCE(SUM(amount_minor),0) FROM refunds WHERE transaction_id=? AND status='completed'",
            (t["id"],),
        ).fetchone()[0]
        if (
            completed == t["amount_minor"]
            and not c.execute(
                "SELECT 1 FROM payment_events WHERE transaction_id=? AND status IN ('refunded','reversed')",
                (t["id"],),
            ).fetchone()
        ):
            original = c.execute(
                "SELECT attempt_id FROM payment_events WHERE transaction_id=? AND status='succeeded' ORDER BY occurred_at DESC LIMIT 1",
                (t["id"],),
            ).fetchone()
            at = utc()
            c.execute(
                "INSERT INTO payment_events VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "service-refund:" + data["id"],
                    t["id"],
                    original["attempt_id"],
                    "refunded",
                    at,
                    at,
                    None,
                    None,
                    json.dumps(
                        {
                            "source": "confirmed_refund_service_record",
                            "refund_id": data["id"],
                        }
                    ),
                ),
            )
        audit(
            c,
            "refund_" + data["status"],
            data["id"],
            {"transaction_id": t["id"], "amount_minor": data["amount_minor"]},
        )
    return {
        "id": data["id"],
        "status": data["status"],
        "notice": "Service record only; no real money moved",
    }


def servicing_report():
    with connect() as c:
        settlements = [
            dict(r)
            for r in c.execute(
                "SELECT s.*,t.merchant_id FROM settlements s JOIN transactions t ON t.id=s.transaction_id"
            )
        ]
        refunds = [dict(r) for r in c.execute("SELECT * FROM refunds")]
    return {
        "settlements": settlements,
        "overdue_settlements": sum(
            r["status"] == "pending" and r["due_at"] < utc() for r in settlements
        ),
        "refunds": refunds,
        "open_refunds": sum(r["status"] in ("requested", "approved") for r in refunds),
        "completed_refund_value_minor": sum(
            r["amount_minor"] for r in refunds if r["status"] == "completed"
        ),
        "refund_notice": "Partial refunds remain successful payments; refund value is reported separately. Full cumulative refunds reconcile the payment to refunded. Service records confirm outcomes; they do not move funds.",
    }


def personalized_draft(action):
    evidence = (
        json.loads(action["evidence"])
        if isinstance(action["evidence"], str)
        else action["evidence"]
    )
    kind = action["kind"]
    cid = action["entity_id"]
    intro = {
        "onboarding_assistance": "Welcome. We can help you get started with your wallet.",
        "re_engagement": "Would you like help using your wallet again?",
        "retention_outreach": "Would you like assistance with your wallet experience?",
        "resolution_follow_up": "Recent payment activity appears successful. Please tell us if you still need assistance.",
    }.get(kind, "Your wallet support team is available.")
    change = evidence.get("frequency_change")
    context = (
        "Your recent transaction activity is lower than in the preceding period."
        if change is not None and change < 0
        else ""
    )
    return {
        "subject": f"CareFlow support for {cid}",
        "body": " ".join(
            x
            for x in [
                intro,
                context,
                "You can update your communication preferences at any time.",
            ]
            if x
        ),
        "entity_id": cid,
        "segments": evidence.get("segments", []),
        "source": "Evidence-constrained personalized template; no compensation or resolution promises",
    }


def resolution_allowed(c, action, current):
    evidence = (
        json.loads(action["evidence"])
        if isinstance(action["evidence"], str)
        else action["evidence"]
    )
    if (
        not current
        or not current["behavior"]["frequency_30d"]
        or not c.execute(
            "SELECT 1 FROM incidents WHERE action_id=? AND entity_id=? AND status='resolved'",
            (evidence.get("resolved_investigation"), action["entity_id"]),
        ).fetchone()
    ):
        raise ValueError("Reviewed incident resolution is no longer valid")
