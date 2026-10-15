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


def call(fn, *args):
    try:
        return fn(*args)
    except ValueError as e:
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
    cutoff = store.utc((datetime.fromisoformat(now) - timedelta(days=7)).isoformat())
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
    first = events.sort_values(["occurred_at", "event_id"]).drop_duplicates(
        "attempt_id"
    )
    recent = first[
        (first.occurred_at > cutoff)
        & (first.occurred_at < now)
        & (first.received_at <= first.occurred_at)
        & first.status.isin(["succeeded", "failed", "timeout"])
    ]
    customer = recent[recent.customer_id == data.customer_id]
    merchant = recent[recent.merchant_id == data.merchant_id]
    row = {
        "amount_minor": data.amount_minor,
        "hour": datetime.fromisoformat(now).hour,
        "channel_ussd": int(data.channel == "ussd"),
        "channel_qr": int(data.channel == "qr"),
        "customer_prior_attempts": len(customer),
        "customer_prior_failure_rate": float(
            customer.status.isin(["failed", "timeout"]).mean()
        )
        if len(customer)
        else 0,
        "merchant_prior_failure_rate": float(
            merchant.status.isin(["failed", "timeout"]).mean()
        )
        if len(merchant)
        else 0,
    }
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
    return {
        "as_of": s["as_of"],
        "synthetic": True,
        "reliability_7d": s["windows"]["7d"],
        "customers": len(s["customers"]),
        "merchants": len(s["merchants"]),
        "open_actions": sum(a["status"] in decisions.OPEN for a in actions),
        "high_priority_actions": [
            a
            for a in actions
            if a["priority"] == "high" and a["status"] in decisions.OPEN
        ],
        "limitations": "No causal conclusions or proven intervention uplift. Executions are dry-run simulations.",
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
