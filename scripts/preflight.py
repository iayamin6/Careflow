"""Read-only native/container readiness checks; never print credentials or train models."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def command_check(name, command):
    try:
        result = subprocess.run(command, capture_output=True, timeout=15, check=False)
        return {
            "check": name,
            "status": "pass" if result.returncode == 0 else "fail",
            "message": "Command responded successfully"
            if result.returncode == 0
            else "Command failed; check the service locally",
        }
    except (OSError, subprocess.TimeoutExpired):
        return {
            "check": name,
            "status": "fail",
            "message": "Unavailable or did not respond within 15 seconds",
        }


def database_check(path):
    if not path.is_file():
        return {
            "check": "wallet_database",
            "status": "warn",
            "message": "Not bootstrapped; run wallet.synthetic using the intended database path",
        }
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as c:
            tables = {
                r[0]
                for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            required = {
                "payment_events",
                "wallet_customers",
                "merchants",
                "operators",
                "batch_runs",
            }
            if not required <= tables:
                raise ValueError("Missing schema")
            healthy = c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        return {
            "check": "wallet_database",
            "status": "pass" if healthy else "fail",
            "message": "Read-only schema/integrity check complete",
        }
    except (sqlite3.Error, ValueError):
        return {
            "check": "wallet_database",
            "status": "fail",
            "message": "Unreadable, invalid or unmigrated wallet database",
        }


def model_check(directory):
    try:
        pointer = directory / "active.json"
        if pointer.exists():
            candidate = json.loads(pointer.read_text())["candidate"]
            if not isinstance(candidate, str) or not candidate.isalnum():
                raise ValueError("Invalid pointer")
            directory = directory / "candidates" / candidate
        present = all(
            (directory / (name + suffix)).is_file()
            for name in ("churn", "payment")
            for suffix in (".pkl", ".json")
        )
        return {
            "check": "model_artifacts",
            "status": "pass" if present else "warn",
            "message": "Both artifact sets are present (not deserialized)"
            if present
            else "Models absent; rules work, but training is needed for predictions",
        }
    except (ValueError, KeyError, OSError):
        return {
            "check": "model_artifacts",
            "status": "fail",
            "message": "Active model pointer is invalid or unreadable",
        }


def tracking_check(path):
    if not path.is_file():
        return {
            "check": "tracking_artifacts",
            "status": "warn",
            "message": "MLflow tracking database not created yet",
        }
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as c:
            locations = [
                r[0]
                for r in c.execute(
                    "SELECT artifact_location FROM experiments WHERE EXISTS (SELECT 1 FROM runs WHERE runs.experiment_id=experiments.experiment_id)"
                )
            ]
        # Absolute native paths do not become valid by mounting the DB in a container.
        missing = sum(
            location.startswith("/") and not Path(location).exists()
            for location in locations
        )
        return {
            "check": "tracking_artifacts",
            "status": "fail" if missing else "pass",
            "message": f"{missing} inaccessible absolute artifact locations"
            if missing
            else "Recorded absolute local artifact locations are accessible; remote URIs not checked",
        }
    except sqlite3.Error:
        return {
            "check": "tracking_artifacts",
            "status": "warn",
            "message": "Tracking schema unavailable",
        }


def run(mode="native", url=None):
    free = shutil.disk_usage(ROOT).free / 1024**3
    rows = [
        {
            "check": "disk_space",
            "status": "fail"
            if mode == "docker" and free < 8
            else "warn"
            if free < 8
            else "pass",
            "message": f"{free:.1f} GiB free; allow at least 8 GiB headroom for the Python/Java/Spark image build",
        }
    ]
    if mode == "docker":
        rows.append(
            command_check(
                "compose_config",
                [
                    "docker",
                    "compose",
                    "-f",
                    str(ROOT / "compose.yaml"),
                    "config",
                    "--quiet",
                ],
            )
        )
        rows.append(
            command_check(
                "docker_engine", ["docker", "info", "--format", "{{.ServerVersion}}"]
            )
        )
    else:
        rows.append(
            database_check(
                Path(os.getenv("CAREFLOW_WALLET_DB", ROOT / "data/wallet.db"))
            )
        )
        rows.append(
            model_check(
                Path(os.getenv("CAREFLOW_MODEL_DIR", ROOT / "ml/wallet_models"))
            )
        )
        rows.append(tracking_check(ROOT / "data/mlflow.db"))
        spark = os.getenv("SPARK_HOME")
        rows.append(
            {
                "check": "spark_runtime",
                "status": "pass"
                if spark
                and (Path(spark) / "bin/spark-submit").is_file()
                and (ROOT / "spark/target/careflow-wallet.jar").is_file()
                else "warn",
                "message": "Spark requires SPARK_HOME, spark-submit and the built Scala jar",
            }
        )
        rows.append(command_check("java", ["java", "-version"]))
    if url:
        try:
            with urllib.request.urlopen(
                url.rstrip("/") + "/health", timeout=5
            ) as response:
                healthy = response.status == 200
            rows.append(
                {
                    "check": "api_health",
                    "status": "pass" if healthy else "fail",
                    "message": "Health endpoint responded",
                }
            )
        except Exception:
            rows.append(
                {
                    "check": "api_health",
                    "status": "fail",
                    "message": "Health endpoint unavailable",
                }
            )
    return {
        "mode": mode,
        "ready": not any(x["status"] == "fail" for x in rows),
        "checks": rows,
        "scope": "Readiness only; does not build containers, verify external delivery, or certify production readiness",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("native", "docker"), default="native")
    parser.add_argument("--url", help="Optional local API base URL")
    args = parser.parse_args()
    report = run(args.mode, args.url)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["ready"] else 1)
