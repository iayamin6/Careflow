"""Point-in-time observed metrics. Evidence describes association, never causation."""

from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from .store import connect, utc

BAD = {"failed", "timeout"}
SUCCESS = {"succeeded", "refunded", "reversed"}


def frames(as_of=None):
    at = utc(as_of)
    with connect() as c:
        events = pd.read_sql_query(
            """SELECT e.*,t.customer_id,t.merchant_id,t.amount_minor,t.channel
          FROM payment_events e JOIN transactions t ON t.id=e.transaction_id
          WHERE e.occurred_at<=? AND e.received_at<=?""",
            c,
            params=(at, at),
        )
        customers = pd.read_sql_query(
            "SELECT * FROM wallet_customers WHERE joined_at<=?", c, params=(at,)
        )
        merchants = pd.read_sql_query(
            "SELECT * FROM merchants WHERE joined_at<=?", c, params=(at,)
        )
    if events.empty:
        return events, events.copy(), customers, merchants
    # Late confirmations supersede pending/failure. Success is sticky; refund/reversal is explicit.
    events["rank"] = events.status.map(
        {
            "pending": 0,
            "failed": 1,
            "timeout": 1,
            "succeeded": 2,
            "refunded": 3,
            "reversed": 3,
        }
    )
    attempts = (
        events.sort_values(["rank", "occurred_at", "event_id"])
        .drop_duplicates("attempt_id", keep="last")
        .copy()
    )
    first = events.groupby("attempt_id").occurred_at.min()
    attempts["started_at"] = attempts.attempt_id.map(first)
    attempts["had_timeout"] = attempts.attempt_id.isin(
        events.loc[events.status == "timeout", "attempt_id"]
    )
    tx = (
        attempts.sort_values(["rank", "occurred_at", "event_id"])
        .drop_duplicates("transaction_id", keep="last")
        .copy()
    )
    tx["started_at"] = tx.transaction_id.map(
        events.groupby("transaction_id").occurred_at.min()
    )
    return attempts, tx, customers, merchants


def window(df, at, days, end_days=0):
    if df.empty:
        return df.copy()
    end = datetime.fromisoformat(at) - timedelta(days=end_days)
    start = datetime.fromisoformat(at) - timedelta(days=days)
    return df[
        (df.started_at > utc(start.isoformat()))
        & (df.started_at <= utc(end.isoformat()))
    ]


def metrics(a, t):
    n = len(a)
    m = len(t)
    return {
        "attempts": n,
        "transactions": m,
        "attempt_success_rate": float(a.status.isin(SUCCESS).mean()) if n else None,
        "failure_rate": float(a.status.isin(BAD).mean()) if n else None,
        "pending_rate": float((a.status == "pending").mean()) if n else None,
        "timeout_rate": float(a.had_timeout.mean()) if n else None,
        "completion_rate": float(t.status.isin(SUCCESS).mean()) if m else None,
        "refund_reversal_rate": float(t.status.isin({"refunded", "reversed"}).mean())
        if m
        else None,
        "retry_rate": float(max(0, n - m) / n) if n else None,
        "latency_p95_ms": float(a.latency_ms.dropna().quantile(0.95))
        if n and a.latency_ms.notna().any()
        else None,
        "successful_value_minor": int(
            t.loc[t.status == "succeeded", "amount_minor"].sum()
        )
        if m
        else 0,
    }


def behavior(t, at):
    successful = t[t.status == "succeeded"] if not t.empty else t
    recent = window(successful, at, 30)
    prior = window(successful, at, 60, 30)
    recency = (
        (
            datetime.fromisoformat(at)
            - datetime.fromisoformat(successful.started_at.max())
        ).total_seconds()
        / 86400
        if len(successful)
        else None
    )
    return {
        "frequency_30d": len(recent),
        "frequency_previous_30d": len(prior),
        "frequency_change": (len(recent) - len(prior)) / len(prior)
        if len(prior)
        else None,
        "recency_days": round(recency, 2) if recency is not None else None,
        "monetary_minor_30d": int(recent.amount_minor.sum()) if len(recent) else 0,
        "average_amount_minor": round(float(recent.amount_minor.mean()), 2)
        if len(recent)
        else None,
        "active_days_30d": int(recent.started_at.str[:10].nunique())
        if len(recent)
        else 0,
        "merchant_diversity_30d": int(recent.merchant_id.nunique())
        if len(recent)
        else 0,
    }


def entity_metrics(a, t, entity, key, at):
    aa = a[a[key] == entity["id"]] if len(a) else a
    tt = t[t[key] == entity["id"]] if len(t) else t
    current = metrics(window(aa, at, 7), window(tt, at, 7))
    baseline = metrics(window(aa, at, 37, 7), window(tt, at, 37, 7))
    b = behavior(tt, at)
    n = current["attempts"]
    bn = baseline["attempts"]
    sr = current["attempt_success_rate"]
    bs = baseline["attempt_success_rate"]
    sufficient = n >= 10 and bn >= 20
    drop = (bs - sr) if sr is not None and bs is not None else None
    # Business threshold plus sampling error: a small merchant is not condemned by one failure.
    standard_error = (
        np.sqrt(bs * (1 - bs) / max(n, 1) + bs * (1 - bs) / max(bn, 1))
        if bs is not None
        else 1
    )
    deteriorating = bool(
        sufficient
        and drop is not None
        and drop > 2 * standard_error
        and (drop > 0.15 or (drop >= 0.02 and n * drop >= 20))
    )
    unresolved = window(tt, at, 7)
    unresolved = (
        tt[
            ((tt.status.isin(BAD)) & tt.transaction_id.isin(unresolved.transaction_id))
            | tt.status.isin({"pending", "timeout"})
        ]
        if len(tt)
        else tt
    )
    stale_pending = (
        unresolved[
            unresolved.status.isin({"pending", "timeout"})
            & (
                unresolved.started_at
                < utc((datetime.fromisoformat(at) - timedelta(days=1)).isoformat())
            )
        ]
        if len(unresolved)
        else unresolved
    )
    reliability_problem = bool(
        len(stale_pending)
        or (
            len(unresolved) >= 3
            and current["completion_rate"] is not None
            and current["completion_rate"] < 0.8
        )
    )
    tenure = (
        datetime.fromisoformat(at) - datetime.fromisoformat(entity["joined_at"])
    ).days
    segments = []
    if tenure < 30:
        segments.append("new")
    if b["recency_days"] is None or b["recency_days"] >= 30:
        segments.append("inactive")
    elif b["frequency_30d"] >= 20:
        segments.append("highly_active")
    elif b["frequency_30d"] >= 5:
        segments.append("regular")
    else:
        segments.append("occasional")
    if b["frequency_change"] is not None and b["frequency_change"] <= -0.4:
        segments.append("declining_engagement")
    if reliability_problem:
        segments.append("payment_affected")
    reliability = 100 * (sr if sr is not None else 0)
    engagement = 100 * min(1, b["frequency_30d"] / max(b["frequency_previous_30d"], 1))
    recency_score = 100 * max(
        0, 1 - (b["recency_days"] if b["recency_days"] is not None else 30) / 30
    )
    health = (
        round(0.5 * reliability + 0.3 * engagement + 0.2 * recency_score, 1)
        if n >= 10 and tenure >= 30
        else None
    )
    result = {
        "id": entity["id"],
        "segments": segments,
        "tenure_days": tenure,
        "health_score": health,
        "health_components": {
            "reliability": round(reliability, 1),
            "relative_engagement": round(engagement, 1),
            "recency": round(recency_score, 1),
        },
        "insufficient_data": not sufficient,
        "reliability_deterioration": deteriorating,
        "operational_problem": reliability_problem,
        "unconfirmed_over_24h": len(stale_pending),
        "unresolved_transactions": len(unresolved),
        "unresolved_definition": "Failed transactions in the last 7 days plus all unconfirmed pending/timeout transactions",
        "reliability_7d": current,
        "baseline_30d": baseline,
        "success_rate_drop": drop,
        "estimated_excess_failed_attempts": round(n * max(drop, 0), 1)
        if drop is not None
        else None,
        "behavior": b,
    }
    if key == "merchant_id":
        recent = window(tt, at, 7)
        previous = window(tt, at, 37, 7)
        result.update(
            category=entity["category"],
            size=entity["size"],
            affected_customers=sorted(unresolved.customer_id.unique().tolist())
            if len(unresolved)
            else [],
            unique_customers_7d=int(recent.customer_id.nunique()) if len(recent) else 0,
            returning_customer_share=float(
                recent.customer_id.isin(previous.customer_id).mean()
            )
            if len(recent)
            else None,
            peak_hour_utc=int(pd.to_datetime(recent.started_at).dt.hour.mode().iloc[0])
            if len(recent)
            else None,
        )
        result["segments"] = (
            ["insufficient_history"]
            if not sufficient
            else ["reliability_deterioration"]
            if deteriorating
            else ["declining"]
            if b["frequency_change"] is not None and b["frequency_change"] < -0.3
            else ["growing"]
            if b["frequency_change"] is not None and b["frequency_change"] > 0.3
            else ["stable"]
        )
    return result


def snapshot(as_of=None):
    at = utc(as_of)
    a, t, cs, ms = frames(at)
    customers = [
        entity_metrics(a, t, c, "customer_id", at) for c in cs.to_dict("records")
    ]
    merchants = [
        entity_metrics(a, t, m, "merchant_id", at) for m in ms.to_dict("records")
    ]
    values = [c["behavior"]["monetary_minor_30d"] for c in customers]
    cutoff = float(np.quantile(values, 0.8)) if values else 0
    for c in customers:
        if (
            c["behavior"]["monetary_minor_30d"] > 0
            and c["behavior"]["monetary_minor_30d"] >= cutoff
        ):
            c["segments"].append("high_value")
    channels = (
        {
            ch: metrics(
                window(a[a.channel == ch], at, 7), window(t[t.channel == ch], at, 7)
            )
            for ch in sorted(a.channel.unique())
        }
        if len(a)
        else {}
    )
    result = {
        "as_of": at,
        "synthetic": True,
        "evidence_notice": "Correlated patterns require investigation; they do not establish technical root cause.",
        "windows": {
            str(d) + "d": metrics(window(a, at, d), window(t, at, d))
            for d in [1, 7, 30]
        },
        "channels_7d": channels,
        "customers": customers,
        "merchants": merchants,
        "daily_trend": [
            {
                "date": (datetime.fromisoformat(at) - timedelta(days=i))
                .date()
                .isoformat(),
                **metrics(window(a, at, i + 1, i), window(t, at, i + 1, i)),
            }
            for i in range(13, -1, -1)
        ],
    }

    from .experience import enrich

    return enrich(result)
