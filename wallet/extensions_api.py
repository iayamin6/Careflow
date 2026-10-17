"""Protected operational and analytical endpoints for the integrated wallet."""

from typing import Literal
from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from . import operations, delivery, experience, batch, governance
from .api import call
from .store import connect, audit
from .security import issue

router = APIRouter(prefix="/wallet", tags=["Wallet operations"])


class IncidentUpdate(BaseModel):
    status: Literal["open", "investigating", "escalated", "resolved"]
    owner: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=2000)


class Override(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    days: int = Field(default=7, ge=1, le=30)


class Preferences(BaseModel):
    channel: Literal["in_app", "email", "webhook"] = "in_app"
    support: bool = True
    marketing: bool = False
    destination: str | None = Field(default=None, max_length=200)


class Settlement(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    transaction_id: str
    due_at: str
    settled_at: str | None = None
    amount_minor: int = Field(gt=0, strict=True)


class Refund(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    transaction_id: str
    amount_minor: int = Field(gt=0, strict=True)
    status: Literal["requested", "approved", "completed", "rejected"] = "requested"


class Complaint(BaseModel):
    customer_id: str
    text: str = Field(min_length=3, max_length=3000)


class Receipt(BaseModel):
    id: str = Field(min_length=1, max_length=150)
    delivery_id: int
    status: Literal["delivered", "bounced"]


class Experiment(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=80)
    name: str = Field(min_length=1, max_length=100)
    customer_ids: list[str] = Field(min_length=2, max_length=10000)
    horizon_days: int = Field(default=30, ge=7, le=90)


class Operator(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: Literal["admin", "analyst", "operations", "support", "automation"]
    days: int = Field(default=30, ge=1, le=365)


@router.get("/session")
def session(request: Request):
    return request.state.operator


@router.post("/operators")
def operator(data: Operator):
    return {
        "token": call(issue, data.name, data.role, data.days),
        "notice": "Shown once; store securely",
    }


@router.post("/operators/{id}/revoke")
def revoke(id: int):
    with connect() as c:
        c.execute("UPDATE operators SET revoked=1 WHERE id=?", (id,))
        audit(c, "operator_revoked", id, {})
    return {"revoked": id}


@router.get("/operators")
def operators():
    with connect() as c:
        return {
            "data": [
                dict(r)
                for r in c.execute(
                    "SELECT id,name,role,expires_at,revoked FROM operators"
                )
            ]
        }


@router.get("/incidents")
def incidents():
    return {"data": operations.incidents()}


@router.post("/incidents/{id}")
def incident(id: int, data: IncidentUpdate):
    return call(operations.update_incident, id, data.status, data.owner, data.note)


@router.post("/actions/{id}/override")
def override(id: int, data: Override):
    return call(operations.override, id, data.reason, data.days)


@router.post("/customers/{id}/preferences")
def preference(id: str, data: Preferences):
    return call(operations.preferences, id, **data.model_dump())


@router.get("/customers/{id}/preferences")
def get_preference(id: str):
    with connect() as c:
        p = c.execute("SELECT * FROM preferences WHERE customer_id=?", (id,)).fetchone()
        return (
            dict(p)
            if p
            else {
                "channel": "in_app",
                "support": True,
                "marketing": None,
                "notice": "Legacy consent applies until category preferences are set",
            }
        )


@router.post("/operators/verify-destination/{id}")
def verify(id: str):
    return call(operations.verify_destination, id)


@router.get("/servicing")
def servicing():
    return operations.servicing_report()


@router.post("/settlements")
def settlement(data: Settlement):
    return call(operations.settlement, data.model_dump())


@router.post("/refunds")
def refund(data: Refund):
    return call(operations.refund, data.model_dump())


@router.post("/complaints")
def complaint(data: Complaint):
    return call(experience.complaint, data.customer_id, data.text)


@router.get("/complaints")
def complaints():
    with connect() as c:
        return {
            "data": [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM complaints ORDER BY id DESC LIMIT 1000"
                )
            ]
        }


@router.post("/complaints/{id}/resolve")
def resolve_complaint(id: int):
    with connect() as c:
        c.execute("UPDATE complaints SET status='resolved' WHERE id=?", (id,))
        audit(c, "complaint_resolved", id, {})
    return {"id": id, "status": "resolved"}


@router.get("/deliveries")
def deliveries():
    with connect() as c:
        return {
            "data": [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM deliveries ORDER BY id DESC LIMIT 1000"
                )
            ]
        }


@router.post("/deliveries/dispatch")
def dispatch():
    return {"data": call(delivery.dispatch)}


@router.post("/deliveries/receipts")
def receipt(data: Receipt):
    return call(delivery.receipt, data.id, data.delivery_id, data.status)


@router.get("/batch")
def latest_batch():
    return batch.latest()


@router.post("/batch/run")
def run_batch(tasks: BackgroundTasks):
    import os

    if not os.getenv("SPARK_HOME"):
        raise HTTPException(409, "SPARK_HOME is not configured")
    if not (batch.config.ROOT / "spark/target/careflow-wallet.jar").exists():
        raise HTTPException(409, "Build the Scala Spark jar first")
    tasks.add_task(batch.run)
    return {
        "status": "scheduled",
        "notice": "Check batch history for completion or failure",
    }


@router.get("/models/monitoring")
def monitoring():
    return governance.monitor()


@router.get("/models/candidates")
def candidates():
    return {"data": governance.candidates()}


@router.post("/models/retrain")
def retrain(tasks: BackgroundTasks):
    tasks.add_task(governance.retrain)
    return {"status": "scheduled", "notice": "Candidate requires explicit promotion"}


@router.post("/models/candidates/{id}/promote")
def promote(id: str):
    return call(governance.promote, id)


@router.post("/experiments")
def experiment(data: Experiment):
    return call(
        experience.create_experiment,
        data.id,
        data.name,
        data.customer_ids,
        data.horizon_days,
    )


@router.get("/experiments")
def experiments():
    with connect() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM experiments")]
    return {"data": [experience.experiment_report(id) for id in ids]}


@router.get("/experiments/{id}")
def experiment_detail(id: str):
    return call(experience.experiment_report, id)
