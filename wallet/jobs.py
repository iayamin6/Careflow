"""Persistent analytical jobs, claimed transactionally by a separate worker.

Interrupted work requires an explicit retry; it is never silently replayed.
"""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta
from . import config
from .store import connect, migrate, utc, audit
from .security import actor

KINDS = {"spark", "retrain"}
LEASE_SECONDS = 120


def enqueue(kind):
    if kind not in KINDS:
        raise ValueError("Unsupported job kind")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute(
            "SELECT * FROM background_jobs WHERE kind=? AND status IN ('queued','running')",
            (kind,),
        ).fetchone()
        if existing:
            return {**dict(existing), "duplicate": True}
        id = uuid.uuid4().hex
        c.execute(
            "INSERT INTO background_jobs(id,kind,status,created_at,requested_by) VALUES(?,?,'queued',?,?)",
            (id, kind, utc(), actor.get()),
        )
        audit(c, "job_queued", id, {"kind": kind})
    return {"id": id, "kind": kind, "status": "queued", "duplicate": False}


def listing():
    with connect() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM background_jobs ORDER BY created_at DESC LIMIT 100"
            )
        ]


def claim(owner):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT * FROM background_jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not row:
            return None
        c.execute(
            "UPDATE background_jobs SET status='running',owner=?,started_at=?,heartbeat_at=?,attempts=attempts+1 WHERE id=?",
            (owner, utc(), utc(), row["id"]),
        )
        audit(c, "job_started", row["id"], {"worker": owner})
        return dict(
            c.execute(
                "SELECT * FROM background_jobs WHERE id=?", (row["id"],)
            ).fetchone()
        )


def heartbeat(owner, id=None):
    with connect() as c:
        c.execute(
            "INSERT INTO workers VALUES(?,?) ON CONFLICT(id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at",
            (owner, utc()),
        )
        if id:
            return (
                c.execute(
                    "UPDATE background_jobs SET heartbeat_at=? WHERE id=? AND owner=? AND status='running'",
                    (utc(), id, owner),
                ).rowcount
                == 1
            )
    return True


def recover():
    cutoff = utc(
        (datetime.fromisoformat(utc()) - timedelta(seconds=LEASE_SECONDS)).isoformat()
    )
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        rows = c.execute(
            "SELECT id FROM background_jobs WHERE status='running' AND heartbeat_at<?",
            (cutoff,),
        ).fetchall()
        for row in rows:
            c.execute(
                "UPDATE background_jobs SET status='interrupted',finished_at=?,error='Worker lease expired; inspect output before retry' WHERE id=?",
                (utc(), row["id"]),
            )
            audit(c, "job_interrupted", row["id"], {"reason": "worker lease expired"})
        return len(rows)


def retry(id):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM background_jobs WHERE id=?", (id,)).fetchone()
        if (
            not row
            or row["status"] not in ("failed", "interrupted")
            or row["attempts"] >= 3
        ):
            raise ValueError(
                "Only failed/interrupted jobs below three attempts can retry"
            )
        if c.execute(
            "SELECT 1 FROM background_jobs WHERE kind=? AND status IN ('queued','running')",
            (row["kind"],),
        ).fetchone():
            raise ValueError("Another job of this kind is already active")
        c.execute(
            "UPDATE background_jobs SET status='queued',owner=NULL,heartbeat_at=NULL,finished_at=NULL,error=NULL,result=NULL WHERE id=?",
            (id,),
        )
        audit(c, "job_retry_requested", id, {})
    return {"id": id, "status": "queued"}


def finish(id, owner, status, result=None, error=None):
    with connect() as c:
        changed = c.execute(
            "UPDATE background_jobs SET status=?,result=?,error=?,finished_at=? WHERE id=? AND owner=? AND status='running'",
            (
                status,
                json.dumps(result) if result is not None else None,
                error,
                utc(),
                id,
                owner,
            ),
        ).rowcount
        if changed:
            audit(c, "job_" + status, id, {"error": error})
        return bool(changed)


def worker_healthy():
    cutoff = utc((datetime.fromisoformat(utc()) - timedelta(seconds=60)).isoformat())
    with connect() as c:
        return bool(
            c.execute(
                "SELECT 1 FROM workers WHERE heartbeat_at>?", (cutoff,)
            ).fetchone()
        )


def perform(kind):
    if kind == "spark":
        from .batch import run

        return run()
    if kind == "retrain":
        from .governance import retrain

        result = retrain()
        if result["status"] == "failed":
            raise RuntimeError(
                "Training candidate failed; inspect candidate training log"
            )
        return result
    raise ValueError("Unsupported job kind")


def run_one(owner, stop):
    job = claim(owner)
    if not job:
        return False
    directory = config.ROOT / "data/jobs" / job["id"]
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / f"result-{job['attempts']}.json"
    env = {
        **os.environ,
        "CAREFLOW_WALLET_DB": str(config.DB_PATH.resolve()),
        "CAREFLOW_MODEL_DIR": str(config.MODEL_DIR.resolve()),
    }
    child = None
    status, result, error = "failed", None, None
    try:
        with (directory / f"attempt-{job['attempts']}.log").open("w") as log:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "wallet.jobs",
                    "--perform",
                    job["kind"],
                    "--result",
                    str(result_path),
                ],
                cwd=config.ROOT,
                env=env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            import time

            deadline = time.monotonic() + 1900
            while child.poll() is None:
                if (
                    stop.wait(2)
                    or time.monotonic() >= deadline
                    or not heartbeat(owner, job["id"])
                ):
                    status = "interrupted"
                    error = "Worker stopped, timed out or lost lease; inspect output before retry"
                    break
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
            elif child.returncode == 0:
                result = json.loads(result_path.read_text())
                status = "succeeded"
            else:
                error = "Job process failed; inspect its attempt log"
    except Exception as exc:
        error = type(exc).__name__ + "; inspect worker/job logs"
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
    finally:
        finish(job["id"], owner, status, result, error)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--perform", choices=sorted(KINDS))
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    migrate()
    if args.check:
        return 0 if worker_healthy() else 1
    if args.perform:
        if not args.result:
            parser.error("--result is required")
        args.result.write_text(json.dumps(perform(args.perform)))
        return 0
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    owner = uuid.uuid4().hex
    context = actor.set("worker:" + owner)
    try:
        while not stop.is_set():
            recover()
            heartbeat(owner)
            worked = run_one(owner, stop)
            if args.once:
                break
            if not worked:
                stop.wait(5)
    finally:
        actor.reset(context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
