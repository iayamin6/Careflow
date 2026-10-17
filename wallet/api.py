import sqlite3
from datetime import datetime, timedelta
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from . import store, analytics, decisions, models

router = APIRouter(prefix="/wallet", tags=["Wallet intelligence"])


class Customer(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    joined_at: str
    consent: bool = False
    synthetic: Literal[True] = True
    legacy_customer_id: str | None = None

    @field_validator("joined_at")
    @classmethod
    def timestamp(cls, v):
        return store.utc(v)


class Merchant(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    category: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    size: Literal["small", "medium", "large"] = "small"
    joined_at: str

    @field_validator("joined_at")
    @classmethod
    def timestamp(cls, v):
        return store.utc(v)


class Event(BaseModel):
    event_id: str = Field(min_length=1, max_length=100)
    transaction_id: str = Field(min_length=1, max_length=100)
    attempt_id: str = Field(min_length=1, max_length=100)
    customer_id: str
    merchant_id: str
    amount_minor: int = Field(gt=0, strict=True)
    currency: Literal["BDT"] = "BDT"
    channel: Literal["app", "qr", "ussd"]
    status: Literal["pending", "failed", "timeout", "succeeded", "refunded", "reversed"]
    occurred_at: str
    latency_ms: int | None = Field(default=None, ge=0)
    failure_category: (
        Literal["network_timeout", "provider_declined", "insufficient_funds", "unknown"]
        | None
    ) = None

    @field_validator("occurred_at")
    @classmethod
    def timestamp(cls, v):
        return store.utc(v)


class Execution(BaseModel):
    execution_key: str = Field(min_length=1, max_length=150)
    simulate_failure: bool = False


class PaymentRequest(BaseModel):
    customer_id: str
    merchant_id: str
    amount_minor: int = Field(gt=0)
    channel: Literal["app", "qr", "ussd"]


def call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (ValueError, sqlite3.IntegrityError) as e:
        raise HTTPException(409, str(e)) from e


@router.post("/customers")
def customer(data: Customer):
    store.put_customer(data.model_dump())
    return {"id": data.id}


@router.post("/merchants")
def merchant(data: Merchant):
    store.put_merchant(data.model_dump())
    return {"id": data.id}


@router.post("/events")
def event(data: Event):
    return call(store.ingest, data.model_dump())


@router.get("/analytics")
def overview():
    return analytics.snapshot()


@router.get("/customers/{id}")
def customer_detail(id: str):
    data = analytics.snapshot()
    row = next((c for c in data["customers"] if c["id"] == id), None)
    if row is None:
        raise HTTPException(404, "Customer not found")
    return {
        **row,
        "payment_risk": models.payment_risks({**data, "customers": [row]}).get(
            id, {"status": "unavailable"}
        ),
        "churn": models.churn_predictions({**data, "customers": [row]}).get(
            id, {"status": "insufficient_history", "probability": None}
        ),
    }


@router.get("/merchants/{id}")
def merchant_detail(id: str):
    row = next((m for m in analytics.snapshot()["merchants"] if m["id"] == id), None)
    if row is None:
        raise HTTPException(404, "Merchant not found")
    return row


@router.post("/decisions/refresh")
def refresh():
    return decisions.refresh()


@router.get("/actions")
def actions():
    return {"data": decisions.list_actions()}


@router.post("/actions/{id}/approve")
def approve(id: int):
    return call(decisions.approve, id)


@router.post("/actions/{id}/execute")
def execute(id: int, data: Execution):
    return call(decisions.execute, id, data.execution_key, data.simulate_failure)


@router.get("/models")
def monitoring():
    return models.monitoring()


@router.post("/payment/predict")
def payment(data: PaymentRequest):
    artifact = models.load("payment")
    if artifact is None:
        return {"status": "unavailable", "probability": None}
    events, cs = models.load_data()
    now = store.utc()
    with store.connect() as c:
        if (
            not c.execute(
                "SELECT 1 FROM wallet_customers WHERE id=?", (data.customer_id,)
            ).fetchone()
            or not c.execute(
                "SELECT 1 FROM merchants WHERE id=?", (data.merchant_id,)
            ).fetchone()
        ):
            raise HTTPException(404, "Customer or merchant not found")
    row = models.pre_attempt_row(
        events, data.customer_id, data.merchant_id, data.amount_minor, data.channel, now
    )
    return models.prediction(artifact, row)


@router.get("/automation")
def automation():
    with store.connect() as c:
        return {
            "mode": "dry_run",
            "executions": [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM executions ORDER BY created_at DESC LIMIT 100"
                )
            ],
            "audit": [
                dict(r)
                for r in c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")
            ],
        }


@router.get("/report")
def report():
    s = analytics.snapshot()
    actions = decisions.list_actions()
    from collections import Counter
    from .operations import servicing_report

    predictions = models.churn_predictions(s)
    with store.connect() as c:
        executions = dict(
            c.execute(
                "SELECT status,COUNT(*) FROM executions GROUP BY status"
            ).fetchall()
        )
        deliveries = dict(
            c.execute(
                "SELECT status,COUNT(*) FROM deliveries GROUP BY status"
            ).fetchall()
        )
        complaints = dict(
            c.execute(
                "SELECT category,COUNT(*) FROM complaints WHERE status='open' GROUP BY category"
            ).fetchall()
        )
    return {
        "as_of": s["as_of"],
        "synthetic": True,
        "reliability_7d": s["windows"]["7d"],
        "customers": len(s["customers"]),
        "merchants": len(s["merchants"]),
        "active_customers": sum(
            c["behavior"]["frequency_30d"] > 0 for c in s["customers"]
        ),
        "successful_volume_minor_30d": sum(
            c["behavior"]["monetary_minor_30d"] for c in s["customers"]
        ),
        "customer_segments": dict(
            Counter(seg for c in s["customers"] for seg in c["segments"])
        ),
        "elevated_churn": sum(
            p.get("probability", 0) >= 0.65
            for p in predictions.values()
            if p.get("probability") is not None
        ),
        "churn_model_available": any(
            p["status"] == "available" for p in predictions.values()
        ),
        "merchants_requiring_attention": [
            m["id"]
            for m in s["merchants"]
            if m["operational_problem"]
            or m["reliability_deterioration"]
            or m.get("overdue_settlements", 0)
        ],
        "merchant_segments": dict(
            Counter(seg for m in s["merchants"] for seg in m["segments"])
        ),
        "open_actions": sum(a["status"] in decisions.OPEN for a in actions),
        "recommended_interventions": dict(
            Counter(a["kind"] for a in actions if a["status"] in decisions.OPEN)
        ),
        "high_priority_actions": [
            a
            for a in actions
            if a["priority"] == "high" and a["status"] in decisions.OPEN
        ],
        "workflow_outcomes": executions,
        "delivery_outcomes": deliveries,
        "open_complaint_categories": complaints,
        "servicing": servicing_report(),
        "reliability_trend": s["daily_trend"],
        "limitations": "Synthetic observational results; neither drafts nor SMTP acceptance prove delivery or causal uplift.",
    }


@router.get("/summary")
def summary():
    from .cache import cached_summary

    return cached_summary(report)


@router.get("/outcomes")
def outcomes():
    now = store.utc()
    a, t, _, _ = analytics.frames(now)
    results = []
    for action in decisions.list_actions():
        if action["status"] != "completed":
            continue
        key = "customer_id" if action["entity_type"] == "customer" else "merchant_id"
        entity = t[t[key] == action["entity_id"]] if len(t) else t
        event_time = action["updated_at"]
        elapsed = (
            datetime.fromisoformat(now) - datetime.fromisoformat(event_time)
        ).total_seconds() / 86400
        before = analytics.window(entity, event_time, 7)
        after = (
            entity[
                (entity.started_at > event_time)
                & (
                    entity.started_at
                    <= store.utc(
                        (
                            datetime.fromisoformat(event_time) + timedelta(days=7)
                        ).isoformat()
                    )
                )
            ]
            if len(entity)
            else entity
        )
        results.append(
            {
                "action_id": action["id"],
                "entity_id": action["entity_id"],
                "days_observed_after": round(elapsed, 2),
                "baseline_successful_transactions": int(
                    (before.status == "succeeded").sum()
                )
                if len(before)
                else 0,
                "after_successful_transactions": int(
                    (after.status == "succeeded").sum()
                )
                if len(after)
                else 0,
                "follow_up_complete": elapsed >= 7,
                "interpretation": "Observed association only; dry-run intervention cannot establish causal uplift",
            }
        )
    return {"data": results}
