"""Matured-label evaluation, drift alerts and reviewable retraining candidates."""

import json
import os
from datetime import datetime, timedelta
import subprocess
import sys
import uuid
from . import models, config
from .store import connect, utc, audit


def log_churn(data, predictions):
    with connect() as c:
        for cid, p in predictions.items():
            if p.get("status") != "available":
                continue
            exists = c.execute(
                "SELECT 1 FROM prediction_log WHERE model='churn' AND entity_id=? AND version=? AND substr(as_of,1,10)=?",
                (cid, p["version"], data["as_of"][:10]),
            ).fetchone()
            if not exists:
                c.execute(
                    "INSERT INTO prediction_log(model,entity_id,version,as_of,probability,features) VALUES(?,?,?,?,?,?)",
                    (
                        "churn",
                        cid,
                        p["version"],
                        data["as_of"],
                        p["probability"],
                        json.dumps(p.get("drivers", [])),
                    ),
                )


def monitor():
    import numpy as np

    events, _ = models.load_data()
    now = utc()
    results = models.monitoring()
    alerts = []
    if len(events) and results.get("payment", {}).get("feature_reference"):
        payment = models.payment_dataset(events)
        payment = payment[
            payment.as_of
            > utc((datetime.fromisoformat(now) - timedelta(days=30)).isoformat())
        ]
        results["payment"]["feature_drift"] = (
            {
                f: round(
                    abs(float(payment[f].mean()) - v["mean"]) / max(v["std"], 1e-6), 3
                )
                for f, v in results["payment"]["feature_reference"].items()
            }
            if len(payment)
            else {}
        )
    for name, r in results.items():
        for feature, shift in r.get("feature_drift", {}).items():
            if shift >= 2:
                alerts.append(
                    {
                        "model": name,
                        "feature": feature,
                        "mean_shift": shift,
                        "action": "Review distribution and matured-label performance before retraining",
                    }
                )
    with connect() as c:
        pending = c.execute(
            "SELECT * FROM prediction_log WHERE model='churn' AND target IS NULL AND as_of<=?",
            (utc((datetime.fromisoformat(now) - timedelta(days=30)).isoformat()),),
        ).fetchall()
        for p in pending:
            end = utc(
                (datetime.fromisoformat(p["as_of"]) + timedelta(days=30)).isoformat()
            )
            success = (
                events[
                    (events.customer_id == p["entity_id"])
                    & (events.occurred_at > p["as_of"])
                    & (events.occurred_at <= end)
                    & (events.received_at <= now)
                    & (events.status == "succeeded")
                ]
                if len(events)
                else events
            )
            c.execute(
                "UPDATE prediction_log SET target=?,evaluated_at=? WHERE id=?",
                (int(success.empty), now, p["id"]),
            )
        rows = [
            dict(r)
            for r in c.execute("SELECT * FROM prediction_log WHERE target IS NOT NULL")
        ]
        groups = {}
        for p in rows:
            groups.setdefault(p["version"], []).append(p)
        matured = {
            version: models.evaluate(
                np.array([p["target"] for p in ps]),
                np.array([p["probability"] for p in ps]),
            )
            for version, ps in groups.items()
            if len(ps) >= 20 and len({p["target"] for p in ps}) > 1
        }
        report = {
            "as_of": now,
            "models": results,
            "alerts": alerts,
            "matured_churn_evaluation": matured,
            "matured_rows": len(rows),
            "evaluation_status": "available"
            if matured
            else "waiting for at least 20 matured observations with both classes",
        }
        c.execute(
            "INSERT INTO monitoring_runs(created_at,report) VALUES(?,?)",
            (now, json.dumps(report)),
        )
    return report


def retrain():
    id = uuid.uuid4().hex
    directory = config.MODEL_DIR / "candidates" / id
    directory.mkdir(parents=True)
    status = {"id": id, "status": "running", "created_at": utc()}
    (directory / "job.json").write_text(json.dumps(status))
    env = {
        **os.environ,
        "CAREFLOW_MODEL_DIR": str(directory),
        "CAREFLOW_WALLET_DB": str(config.DB_PATH),
    }
    try:
        with (directory / "training.log").open("w") as log:
            subprocess.run(
                [sys.executable, "-m", "wallet.models"],
                cwd=config.ROOT,
                env=env,
                stdout=log,
                stderr=log,
                check=True,
                timeout=1200,
            )
        status["status"] = "awaiting_review"
        status["finished_at"] = utc()
    except Exception as e:
        status["status"] = "failed"
        status["error"] = type(e).__name__
        status["finished_at"] = utc()
    (directory / "job.json").write_text(json.dumps(status))
    return status


def candidates():
    return [
        json.loads(p.read_text())
        for p in sorted((config.MODEL_DIR / "candidates").glob("*/job.json"))
    ]


def promote(id):
    import shutil

    if not id.isalnum():
        raise ValueError("Invalid candidate ID")
    directory = config.MODEL_DIR / "candidates" / id
    if not (directory / "job.json").exists():
        raise ValueError("Candidate not found")
    status = json.loads((directory / "job.json").read_text())
    if status["status"] != "awaiting_review":
        raise ValueError("Candidate is not ready for review")
    for name in ("churn", "payment"):
        if not (directory / f"{name}.pkl").is_file():
            raise ValueError("Candidate model artifact is missing")
        r = json.loads((directory / f"{name}.json").read_text())
        if r["test"]["pr_auc"] <= r["baseline_test"]["pr_auc"]:
            raise ValueError("Candidate does not beat its prevalence baseline")
    pointer = config.MODEL_DIR / "active.json"
    staged = config.MODEL_DIR / ("active." + uuid.uuid4().hex + ".staged.json")
    if pointer.exists():
        shutil.copy2(pointer, directory / "previous-active.json")
    staged.write_text(json.dumps({"candidate": id, "promoted_at": utc()}))
    staged.replace(pointer)  # Atomic model-set switch; both artifacts already exist.
    status["status"] = "promoted"
    (directory / "job.json").write_text(json.dumps(status))
    with connect() as c:
        audit(c, "model_candidate_promoted", id, {})
    return status
