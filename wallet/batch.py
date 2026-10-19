"""Bounded-memory export → Scala/Spark → atomically published analytical results."""

import argparse
from datetime import datetime, timedelta
import gzip
import json
import os
from pathlib import Path
import subprocess
import uuid
from . import config
from .store import connect, utc, audit


def export(path, replicas=1):
    path.mkdir(parents=True, exist_ok=False)
    count = 0
    with connect() as c:
        c.execute(
            "BEGIN"
        )  # one consistent SQLite snapshot across dimensions and events
        as_of = utc()
        version = c.execute(
            "SELECT COALESCE(MAX(rowid),0) FROM payment_events"
        ).fetchone()[0]
        for table, name in [
            ("wallet_customers", "customers"),
            ("merchants", "merchants"),
        ]:
            with (path / (name + ".json")).open("w") as f:
                for row in c.execute("SELECT * FROM " + table):
                    f.write(json.dumps(dict(row)) + "\n")
        with gzip.open(path / "events.json.gz", "wt") as f:
            for replica in range(replicas):
                for row in c.execute(
                    "SELECT e.event_id,e.transaction_id,e.attempt_id,e.status,e.occurred_at,e.received_at,e.latency_ms,t.customer_id,t.merchant_id,t.amount_minor,t.channel FROM payment_events e JOIN transactions t ON t.id=e.transaction_id"
                ):
                    record = dict(row)
                    if replicas > 1:
                        for key in ("event_id", "transaction_id", "attempt_id"):
                            record[key] += f"-replica{replica}"
                    f.write(json.dumps(record) + "\n")
                    count += 1
    return as_of, version, count


def run(replicas=1):
    if not 1 <= replicas <= 100:
        raise ValueError("Replicas must be 1–100")
    spark = os.getenv("SPARK_HOME")
    if not spark:
        raise ValueError("SPARK_HOME is not configured")
    jar = config.ROOT / "spark/target/careflow-wallet.jar"
    if not jar.exists():
        raise ValueError("Build the Scala Spark jar first")
    id = uuid.uuid4().hex
    base = Path(os.getenv("CAREFLOW_BATCH_DIR", config.ROOT / "data/batch")) / id
    source = base / "input"
    output = base / "output"
    at, version, count = export(source, replicas)
    with connect() as c:
        c.execute(
            "INSERT INTO batch_runs VALUES(?,?,?,?,?,?,?,NULL)",
            (id, "running", at, count, str(output), None, utc()),
        )
    try:
        env = os.environ.copy()
        env.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        with (base / "spark.log").open("w") as log:
            subprocess.run(
                [
                    str(Path(spark) / "bin/spark-submit"),
                    "--master",
                    os.getenv("CAREFLOW_SPARK_MASTER", "local[2]"),
                    "--driver-memory",
                    "1g",
                    "--conf",
                    "spark.ui.enabled=false",
                    "--class",
                    "careflow.WalletAnalytics",
                    str(jar),
                    str(source),
                    str(output),
                    at,
                ],
                env=env,
                stdout=log,
                stderr=log,
                check=True,
                timeout=1800,
            )

        def records(name):
            for file in sorted((output / (name + ".json")).glob("part-*.json")):
                with file.open() as f:
                    for line in f:
                        yield json.loads(line)

        with connect() as c:
            c.execute("BEGIN IMMEDIATE")
            for name, key, typ in [
                ("customers", "customer_id", "customer"),
                ("merchants", "merchant_id", "merchant"),
                ("reliability", "days", "reliability"),
            ]:
                for row in records(name):
                    row["event_version"] = version
                    c.execute(
                        "INSERT INTO batch_metrics VALUES(?,?,?,?)",
                        (id, typ, str(row[key]), json.dumps(row)),
                    )
            status = "succeeded" if replicas == 1 else "benchmark"
            c.execute(
                "UPDATE batch_runs SET status=?,finished_at=? WHERE id=?",
                (status, utc(), id),
            )
            audit(
                c,
                "spark_batch_completed",
                id,
                {"rows": count, "replicas": replicas, "event_version": version},
            )
        manifest = list(records("manifest"))[0]
        return {
            "id": id,
            "status": status,
            **manifest,
            "output_path": str(output),
            "replicas": replicas,
        }
    except Exception as e:
        with connect() as c:
            c.execute(
                "UPDATE batch_runs SET status='failed',error=?,finished_at=? WHERE id=?",
                (type(e).__name__ + ": " + str(e)[:400], utc(), id),
            )
            audit(c, "spark_batch_failed", id, {"error": type(e).__name__})
        raise


def latest():
    with connect() as c:
        runs = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM batch_runs ORDER BY created_at DESC LIMIT 20"
            )
        ]
        good_row = c.execute(
            "SELECT * FROM batch_runs WHERE status='succeeded' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        good = dict(good_row) if good_row else None
        rows = (
            [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM batch_metrics WHERE run_id=?", (good["id"],)
                )
            ]
            if good
            else []
        )
        version = c.execute(
            "SELECT COALESCE(MAX(rowid),0) FROM payment_events"
        ).fetchone()[0]
    metrics = [{**r, "metrics": json.loads(r["metrics"])} for r in rows]
    stale = bool(
        good
        and (
            (metrics and metrics[0]["metrics"]["event_version"] != version)
            or datetime.fromisoformat(good["as_of"])
            < datetime.fromisoformat(utc()) - timedelta(days=1)
        )
    )
    return {
        "status": "available" if good else "unavailable",
        "stale": stale,
        "run": good,
        "metrics": metrics,
        "runs": runs,
    }


if __name__ == "__main__":
    from .store import migrate

    parser = argparse.ArgumentParser()
    parser.add_argument("--replicas", type=int, default=1)
    args = parser.parse_args()
    migrate()
    print(json.dumps(run(args.replicas), indent=2))
