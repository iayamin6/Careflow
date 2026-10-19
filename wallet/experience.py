"""Interpretable complaint routing, calendar baselines and scenario-based value."""

import re
import hashlib
import math
from datetime import datetime, timedelta
from .store import connect, utc, audit

WORDS = {
    "refund_problem": ("refund", "reversal", "reversed"),
    "account_access": ("login", "password", "locked", "access"),
    "merchant_payment": ("merchant", "shop", "checkout", "qr"),
    "delayed_transaction": ("pending", "delay", "waiting", "timeout"),
    "failed_payment": ("failed", "declined", "failure", "unsuccessful"),
}


def classify(text):
    tokens = set(re.findall(r"[a-z]+", text.lower()))
    scores = {
        category: len(tokens.intersection(words)) for category, words in WORDS.items()
    }
    category = max(scores, key=scores.get)
    hits = scores[category]
    return {
        "category": category if hits else "general_support",
        "confidence": min(0.9, 0.5 + 0.1 * hits) if hits else 0.2,
        "sentiment": "negative"
        if tokens.intersection(
            {"angry", "bad", "failed", "frustrated", "terrible", "delay", "stuck"}
        )
        else "neutral",
        "method": "keyword baseline; confidence is a routing heuristic, not a calibrated probability",
    }


def complaint(customer_id, text):
    result = classify(text)
    with connect() as c:
        if not c.execute(
            "SELECT 1 FROM wallet_customers WHERE id=?", (customer_id,)
        ).fetchone():
            raise ValueError("Customer not found")
        cur = c.execute(
            "INSERT INTO complaints(customer_id,text,category,sentiment,confidence,created_at) VALUES(?,?,?,?,?,?)",
            (
                customer_id,
                text,
                result["category"],
                result["sentiment"],
                result["confidence"],
                utc(),
            ),
        )
        audit(c, "complaint_received", cur.lastrowid, {"category": result["category"]})
        return {"id": cur.lastrowid, **result}


def enrich(data):
    """Calendar-matched same-weekday baseline; personal cadence tempers inactivity flags."""
    from .analytics import frames
    import pandas as pd

    _, t, _, merchants = frames(data["as_of"])
    at = pd.Timestamp(data["as_of"])
    with connect() as c:
        complaints = [
            dict(r)
            for r in c.execute(
                "SELECT customer_id,category,COUNT(*) AS count FROM complaints WHERE status='open' GROUP BY customer_id,category"
            )
        ]
        delayed = [
            dict(r)
            for r in c.execute(
                "SELECT t.merchant_id,COUNT(*) AS count FROM settlements s JOIN transactions t ON t.id=s.transaction_id WHERE s.status='pending' AND s.due_at<? GROUP BY t.merchant_id",
                (data["as_of"],),
            )
        ]
    for typ, key in [("customers", "customer_id"), ("merchants", "merchant_id")]:
        for row in data[typ]:
            entity = t[t[key] == row["id"]] if len(t) else t
            success = (
                entity[entity.status == "succeeded"].copy() if len(entity) else entity
            )
            times = (
                pd.to_datetime(success.started_at, utc=True).sort_values()
                if len(success)
                else pd.Series([], dtype="datetime64[ns, UTC]")
            )
            gaps = times.diff().dropna().dt.total_seconds() / 86400
            cadence = float(gaps.median()) if len(gaps) >= 3 else None
            row["cadence_days"] = round(cadence, 2) if cadence is not None else None
            row["expected_inactivity_days"] = (
                max(30, round(2.5 * cadence)) if cadence else 30
            )
            row["inactivity_beyond_cadence"] = (
                row["behavior"]["recency_days"] is None
                or row["behavior"]["recency_days"] >= row["expected_inactivity_days"]
            )
            # Compare current 7d with four equal weekday-aligned windows; never future data.
            buckets = [
                int(
                    (
                        (times > at - pd.Timedelta(days=7 * (i + 1)))
                        & (times <= at - pd.Timedelta(days=7 * i))
                    ).sum()
                )
                for i in range(5)
            ]
            baseline = sum(buckets[1:]) / 4
            row["calendar_baseline"] = {
                "current_7d": buckets[0],
                "expected_7d": baseline,
                "ratio": buckets[0] / baseline if baseline else None,
                "method": "Previous four complete 7-day periods, matching weekday mix",
            }
            if typ == "customers":
                recent_success = (
                    success[
                        success.started_at
                        > (at - pd.Timedelta(days=30)).isoformat(
                            timespec="microseconds"
                        )
                    ]
                    if len(success)
                    else success
                )
                category_map = (
                    merchants.set_index("id").category.to_dict()
                    if len(merchants)
                    else {}
                )
                row["preferred_categories_30d"] = (
                    recent_success.merchant_id.map(category_map)
                    .value_counts()
                    .head(3)
                    .to_dict()
                    if len(recent_success)
                    else {}
                )
                row["complaints"] = [
                    x for x in complaints if x["customer_id"] == row["id"]
                ]
                row["open_complaints"] = sum(x["count"] for x in row["complaints"])
                row["value_scenario"] = {
                    "annual_payment_volume_minor": row["behavior"]["monetary_minor_30d"]
                    * 12,
                    "assumed_margin_rate": 0.005,
                    "annual_contribution_minor": round(
                        row["behavior"]["monetary_minor_30d"] * 12 * 0.005
                    ),
                    "notice": "Scenario at 0.5% assumed margin; not observed revenue or validated lifetime value",
                }
            else:
                row["overdue_settlements"] = sum(
                    x["count"] for x in delayed if x["merchant_id"] == row["id"]
                )
    for row in data["merchants"]:
        peers = [
            m
            for m in data["merchants"]
            if m["category"] == row["category"]
            and m["size"] == row["size"]
            and m["id"] != row["id"]
            and not m["insufficient_data"]
        ]
        values = [
            m["reliability_7d"]["attempt_success_rate"]
            for m in peers
            if m["reliability_7d"]["attempt_success_rate"] is not None
        ]
        row["peer_benchmark"] = {
            "peer_count": len(values),
            "category": row["category"],
            "size": row["size"],
            "success_rate_mean": sum(values) / len(values)
            if len(values) >= 3
            else None,
            "status": "available" if len(values) >= 3 else "insufficient_peers",
        }
    from .batch import latest

    batch = latest()
    data["spark_batch"] = {
        "status": batch["status"],
        "stale": batch["stale"],
        "run_id": batch["run"]["id"] if batch["run"] else None,
        "as_of": batch["run"]["as_of"] if batch["run"] else None,
    }
    batch_customers = {
        r["entity_id"]: r["metrics"]
        for r in batch["metrics"]
        if r["entity_type"] == "customer"
    }
    for customer in data["customers"]:
        customer["batch_engagement"] = batch_customers.get(customer["id"])
    return data


def create_experiment(id, name, customer_ids, horizon_days=30):
    if (
        not customer_ids
        or len(set(customer_ids)) != len(customer_ids)
        or not 7 <= horizon_days <= 90
    ):
        raise ValueError("Unique customer cohort and 7–90 day horizon required")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute(
            "INSERT INTO experiments(id,name,created_at,horizon_days) VALUES(?,?,?,?)",
            (id, name, utc(), horizon_days),
        )
        for customer in customer_ids:
            arm = (
                "treatment"
                if int(hashlib.sha256((id + ":" + customer).encode()).hexdigest(), 16)
                % 2
                else "control"
            )
            c.execute(
                "INSERT INTO assignments VALUES(?,?,?,?)", (id, customer, arm, utc())
            )
        audit(
            c,
            "experiment_created",
            id,
            {"size": len(customer_ids), "horizon_days": horizon_days},
        )
    return experiment_report(id)


def experiment_report(id):
    from .analytics import frames

    _, t, _, _ = frames(utc())
    with connect() as c:
        e = c.execute("SELECT * FROM experiments WHERE id=?", (id,)).fetchone()
        if not e:
            raise ValueError("Experiment not found")
        rows = [
            dict(r)
            for r in c.execute("SELECT * FROM assignments WHERE experiment_id=?", (id,))
        ]
    now = datetime.fromisoformat(utc())
    counts = {
        arm: {"assigned": 0, "matured": 0, "active": 0}
        for arm in ("treatment", "control")
    }
    for r in rows:
        group = counts[r["arm"]]
        group["assigned"] += 1
        end = datetime.fromisoformat(r["assigned_at"]) + timedelta(
            days=e["horizon_days"]
        )
        if now < end:
            continue
        group["matured"] += 1
        if len(t):
            group["active"] += int(
                (
                    (t.customer_id == r["customer_id"])
                    & (t.status == "succeeded")
                    & (t.started_at > r["assigned_at"])
                    & (t.started_at <= utc(end.isoformat()))
                ).any()
            )
    for g in counts.values():
        g["rate"] = g["active"] / g["matured"] if g["matured"] else None
    treatment, control = counts["treatment"], counts["control"]
    lift = None
    interval = None
    if treatment["matured"] >= 20 and control["matured"] >= 20:
        lift = treatment["rate"] - control["rate"]
        se = math.sqrt(
            sum(g["rate"] * (1 - g["rate"]) / g["matured"] for g in counts.values())
        )
        interval = [max(-1, lift - 1.96 * se), min(1, lift + 1.96 * se)]
    return {
        **dict(e),
        "arms": counts,
        "retention_difference": lift,
        "approximate_95pct_interval": interval,
        "assignments": rows,
        "interpretation": "Randomized holdout infrastructure. Synthetic/dry-run results cannot establish real intervention efficacy; at least 20 matured customers per arm required for an estimate.",
    }
