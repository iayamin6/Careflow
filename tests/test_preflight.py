import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from scripts import preflight


class PreflightTest(unittest.TestCase):
    def test_absent_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "absent.db"
            self.assertEqual(preflight.database_check(path)["status"], "warn")
            self.assertFalse(path.exists())

    def test_model_pointer_cannot_escape_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for candidate in ("../outside", 123):
                (directory / "active.json").write_text(
                    json.dumps({"candidate": candidate})
                )
                self.assertEqual(preflight.model_check(directory)["status"], "fail")

    def test_native_tracking_path_is_detected_after_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mlflow.db"
            with sqlite3.connect(path) as c:
                c.execute(
                    "CREATE TABLE experiments(experiment_id INTEGER, artifact_location TEXT)"
                )
                c.execute(
                    "INSERT INTO experiments VALUES(1,?)",
                    (str(Path(temp) / "missing-host-artifacts"),),
                )
                c.execute("CREATE TABLE runs(experiment_id INTEGER)")
                c.execute("INSERT INTO runs VALUES(1)")
            self.assertEqual(preflight.tracking_check(path)["status"], "fail")

    def test_failed_commands_do_not_expose_output(self):
        with patch(
            "scripts.preflight.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 1, stdout=b"private-value", stderr=b"private-value"
            ),
        ):
            report = preflight.command_check("service", ["example"])
            self.assertEqual(report["status"], "fail")
            self.assertNotIn("private-value", json.dumps(report))
        with patch(
            "scripts.preflight.subprocess.run",
            side_effect=subprocess.TimeoutExpired("example", 15),
        ):
            self.assertEqual(
                preflight.command_check("service", ["example"])["status"], "fail"
            )
