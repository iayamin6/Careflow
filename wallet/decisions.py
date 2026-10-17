"""Shared decision policy and transactional, dry-run intervention execution."""

import json
from datetime import datetime, timedelta
from .store import connect, audit, utc
from .analytics import snapshot

OPEN = ("pending", "approved", "retry", "executing")


def recommend(data, predictions=None, payment_risks=None):
    predictions = predictions or {}
    payment_risks = payment_risks or {}
    result = []
    covered = set()

    def add(typ, id, kind, priority, evidence, approval=False):
        result.append(
            {
                "entity_type": typ,
                "entity_id": id,
                "kind": kind,
                "priority": priority,
                "evidence": evidence,
                "approval_required": approval,
            }
        )

    for m in data["merchants"]:
        if (
            (m["reliability_deterioration"] or m["operational_problem"])
            and len(m["affected_customers"]) >= 3
            and not m["insufficient_data"]
        ) or m.get("overdue_settlements", 0):
            covered.update(m["affected_customers"])
            add(
                "merchant",
                m["id"],
                "merchant_investigation",
                "high",
                {
                    "unresolved_transactions": m["unresolved_transactions"],
                    "overdue_settlements": m.get("overdue_settlements", 0),
                    "affected_customers": m["affected_customers"],
                    "success_rate_drop": m["success_rate_drop"],
                    "assessment": "Possible merchant-related pattern; requires investigation",
                },
            )
    for c in data["customers"]:
        id = c["id"]
        p = predictions.get(id)
        segments = c["segments"]
        evidence = {
            "segments": segments,
            "unresolved_transactions": c["unresolved_transactions"],
            "frequency_change": c["behavior"]["frequency_change"],
            "calendar_baseline": c.get("calendar_baseline"),
            "spark_batch": data.get("spark_batch"),
            "churn_probability": p,
            "payment_risk_scenario": payment_risks.get(id),
            "model_status": "available"
            if p is not None
            else "unavailable; rule-based assessment",
        }
        if id in covered:
            continue  # merchant incident owns shared alert; outreach is blocked at execution too
        if c["operational_problem"]:
            add("customer", id, "payment_investigation", "high", evidence)
        elif c["unresolved_transactions"]:
            continue  # insufficient operational evidence does not make marketing appropriate
        elif c.get("open_complaints", 0):
            add(
                "customer",
                id,
                "support_review",
                "normal",
                {**evidence, "complaints": c["complaints"]},
            )
        elif payment_risks.get(id, {}).get("probability", 0) >= 0.3:
            add("customer", id, "payment_risk_review", "normal", evidence)
        elif c["tenure_days"] < 30 and c["behavior"]["frequency_30d"] < 3:
            add("customer", id, "onboarding_assistance", "normal", evidence, True)
        elif "inactive" in segments and c.get("inactivity_beyond_cadence", True):
            add("customer", id, "re_engagement", "normal", evidence, True)
        elif (
            "declining_engagement" in segments or (p is not None and p >= 0.65)
        ) and not (
            "inactive" in segments and not c.get("inactivity_beyond_cadence", True)
        ):
            add("customer", id, "retention_outreach", "normal", evidence, True)
    return result


def refresh(as_of=None):
    data = snapshot(as_of)
    from .models import churn_predictions, payment_risks

    predictions = churn_predictions(data)
    forecasts = payment_risks(data)
    from .governance import log_churn

    log_churn(data, predictions)
    desired = recommend(
        data,
        {
            k: v["probability"]
            for k, v in predictions.items()
            if v["status"] == "available"
        },
        forecasts,
    )
    now = utc()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        # Include reviewed resolution follow-up in the desired set so later
        # refreshes preserve its approval instead of superseding it immediately.
        for customer in data["customers"]:
            if (
                customer["unresolved_transactions"]
                or not customer["behavior"]["frequency_30d"]
            ):
                continue
            prior = c.execute(
                "SELECT a.id FROM actions a JOIN incidents i ON i.action_id=a.id WHERE a.entity_type='customer' AND a.entity_id=? AND a.kind='payment_investigation' AND a.status='completed' AND i.status='resolved' ORDER BY a.id DESC LIMIT 1",
                (customer["id"],),
            ).fetchone()
            if (
                not prior
                or c.execute(
                    "SELECT 1 FROM actions WHERE entity_id=? AND kind='resolution_follow_up' AND status IN ('completed','dismissed') AND json_extract(evidence,'$.resolved_investigation')=?",
                    (customer["id"], prior[0]),
                ).fetchone()
            ):
                continue
            desired.append(
                {
                    "entity_type": "customer",
                    "entity_id": customer["id"],
                    "kind": "resolution_follow_up",
                    "priority": "normal",
                    "approval_required": True,
                    "evidence": {
                        "resolved_investigation": prior[0],
                        "observed_successful_transactions": customer["behavior"][
                            "frequency_30d"
                        ],
                    },
                }
            )
        keys = {(d["entity_type"], d["entity_id"], d["kind"]) for d in desired}
        old = c.execute(
            "SELECT * FROM actions WHERE status IN ('pending','approved','retry')"
        ).fetchall()
        for a in old:
            if (a["entity_type"], a["entity_id"], a["kind"]) not in keys:
                c.execute(
                    "UPDATE actions SET status='superseded',updated_at=? WHERE id=?",
                    (now, a["id"]),
                )
                audit(
                    c,
                    "action_superseded",
                    a["id"],
                    {"reason": "Current evidence no longer supports this action"},
                )
        for d in desired:
            key = (d["entity_type"], d["entity_id"], d["kind"])
            suppressed = c.execute(
                "SELECT 1 FROM overrides WHERE entity_type=? AND entity_id=? AND kind=? AND expires_at>?",
                (*key, now),
            ).fetchone()
            if suppressed:
                continue
            existing = c.execute(
                "SELECT id FROM actions WHERE entity_type=? AND entity_id=? AND kind=? AND status IN ('pending','approved','retry','executing')",
                key,
            ).fetchone()
            if existing:
                c.execute(
                    "UPDATE actions SET evidence=?,updated_at=? WHERE id=?",
                    (json.dumps(d["evidence"]), now, existing[0]),
                )
                continue
            recent = c.execute(
                "SELECT id FROM actions WHERE entity_type=? AND entity_id=? AND kind=? AND status='completed' AND updated_at>?",
                (
                    *key,
                    utc((datetime.fromisoformat(now) - timedelta(days=7)).isoformat()),
                ),
            ).fetchone()
            if recent:
                continue
            c.execute(
                "INSERT INTO actions(entity_type,entity_id,kind,priority,evidence,status,approval_required,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    *key,
                    d["priority"],
                    json.dumps(d["evidence"]),
                    "pending",
                    d["approval_required"],
                    now,
                    now,
                ),
            )
            audit(
                c,
                "action_created",
                c.execute("SELECT last_insert_rowid()").fetchone()[0],
                d,
            )
    return {
        "snapshot": data,
        "models": predictions,
        "payment_forecasts": forecasts,
        "actions": list_actions(),
    }


def list_actions():
    with connect() as c:
        rows = [
            dict(r)
            for r in c.execute("SELECT * FROM actions ORDER BY id DESC LIMIT 1000")
        ]
    for r in rows:
        r["evidence"] = json.loads(r["evidence"])
    return rows


def approve(id):
    with connect() as c:
        cursor = c.execute(
            "UPDATE actions SET approved=1,status='approved',updated_at=? WHERE id=? AND status='pending'",
            (utc(), id),
        )
        if not cursor.rowcount:
            raise ValueError("Action is absent or not pending")
        audit(c, "action_approved", id, {})
    return {"id": id, "status": "approved"}


def execute(id, key, simulate_failure=False):
    # Recompute evidence immediately before dispatch. Persisted approvals alone are insufficient.
    with connect() as c:
        event_version = c.execute(
            "SELECT COALESCE(MAX(rowid),0) FROM payment_events"
        ).fetchone()[0]
        target = c.execute(
            "SELECT entity_type,entity_id FROM actions WHERE id=?", (id,)
        ).fetchone()
    if not target:
        raise ValueError("Action is not executable")
    data = snapshot()
    # Merchant policy uses observed operations. Customer eligibility needs only that
    # customer's predictions; keep every merchant to retain shared-incident precedence.
    model_data = {
        **data,
        "customers": [
            x
            for x in data["customers"]
            if target["entity_type"] == "customer" and x["id"] == target["entity_id"]
        ],
    }
    now = utc()
    from .models import churn_predictions, payment_risks

    churn = churn_predictions(model_data) if model_data["customers"] else {}
    current_recommendations = recommend(
        data,
        {k: v["probability"] for k, v in churn.items() if v["status"] == "available"},
        payment_risks(model_data) if model_data["customers"] else {},
    )
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        previous = c.execute(
            "SELECT * FROM executions WHERE execution_key=?", (key,)
        ).fetchone()
        if previous:
            if previous["action_id"] != id:
                raise ValueError("Execution key belongs to another action")
            return dict(previous)
        if (
            c.execute("SELECT COALESCE(MAX(rowid),0) FROM payment_events").fetchone()[0]
            != event_version
        ):
            raise ValueError(
                "Payment evidence changed during eligibility check; retry with fresh evidence"
            )
        a = c.execute("SELECT * FROM actions WHERE id=?", (id,)).fetchone()
        if not a or a["status"] not in ("pending", "approved", "retry"):
            raise ValueError("Action is not executable")
        if a["approval_required"] and not a["approved"]:
            raise ValueError("Human approval required")
        if a["attempts"] >= 3:
            raise ValueError("Retry limit reached")
        if a["next_retry_at"] and a["next_retry_at"] > now:
            raise ValueError("Retry backoff has not elapsed")
        communication = a["kind"] in (
            "retention_outreach",
            "re_engagement",
            "onboarding_assistance",
            "resolution_follow_up",
        )
        current = next(
            (x for x in data["customers"] if x["id"] == a["entity_id"]), None
        )
        if communication:
            from .operations import communication_allowed

            communication_allowed(c, a)
            customer = c.execute(
                "SELECT * FROM wallet_customers WHERE id=?", (a["entity_id"],)
            ).fetchone()
            if not customer or not customer["consent"]:
                raise ValueError("Communication consent is missing")
            if not current or current["unresolved_transactions"]:
                raise ValueError("Unresolved payment issue blocks outreach")
            if any(
                a["entity_id"] in m["affected_customers"]
                for m in data["merchants"]
                if m["operational_problem"] or m["reliability_deterioration"]
            ):
                raise ValueError("Merchant incident blocks outreach")
            desired = {(d["entity_id"], d["kind"]) for d in current_recommendations}
            if (
                a["kind"] != "resolution_follow_up"
                and (a["entity_id"], a["kind"]) not in desired
            ):
                raise ValueError("Recommendation is obsolete; refresh decisions")
            cutoff = utc((datetime.fromisoformat(now) - timedelta(days=7)).isoformat())
            contact = c.execute(
                "SELECT 1 FROM executions e JOIN actions a ON a.id=e.action_id WHERE a.entity_type='customer' AND a.entity_id=? AND a.approval_required=1 AND e.status='succeeded' AND e.created_at>?",
                (a["entity_id"], cutoff),
            ).fetchone()
            if contact:
                raise ValueError("Seven-day contact frequency limit reached")
            if a["kind"] == "resolution_follow_up":
                from .operations import resolution_allowed

                resolution_allowed(c, a, current)
        else:
            if (a["entity_type"], a["entity_id"], a["kind"]) not in {
                (d["entity_type"], d["entity_id"], d["kind"])
                for d in current_recommendations
            }:
                raise ValueError(
                    "Operational recommendation is obsolete; refresh decisions"
                )
        attempts = a["attempts"] + 1
        status = "failed" if simulate_failure else "succeeded"
        action_status = (
            ("failed" if attempts >= 3 else "retry")
            if simulate_failure
            else "completed"
        )
        detail = json.dumps(
            {
                "dry_run": True,
                "message": "Simulated service outage"
                if simulate_failure
                else "Recorded in dry-run outbox; no external message sent",
                "draft": draft(dict(a)) if communication else a["evidence"],
            }
        )
        c.execute(
            "INSERT INTO executions VALUES(?,?,?,?,?,?)",
            (key, id, status, "dry_run", now, detail),
        )
        retry = (
            utc(
                (
                    datetime.fromisoformat(now)
                    + timedelta(seconds=30 * 2 ** (attempts - 1))
                ).isoformat()
            )
            if action_status == "retry"
            else None
        )
        c.execute(
            "UPDATE actions SET status=?,attempts=?,next_retry_at=?,last_error=?,updated_at=? WHERE id=?",
            (
                action_status,
                attempts,
                retry,
                "Simulated service outage" if simulate_failure else None,
                now,
                id,
            ),
        )
        if communication and not simulate_failure:
            from .delivery import enqueue

            enqueue(c, key, a)
        audit(
            c,
            "workflow_" + status,
            id,
            {"key": key, "transport": "dry_run", "attempt": attempts},
        )
        return dict(
            c.execute(
                "SELECT * FROM executions WHERE execution_key=?", (key,)
            ).fetchone()
        )


def draft(action):
    from .operations import personalized_draft

    return personalized_draft(action)
