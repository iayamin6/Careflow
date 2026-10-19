"""Cross-engine reconciliation against independently calculated SQLite/pandas results."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wallet import batch, analytics

result = batch.latest()
assert result["status"] == "available"
snapshot = analytics.snapshot(result["run"]["as_of"])
checks = 0
for entry in result["metrics"]:
    row = entry["metrics"]
    typ = entry["entity_type"]
    id = entry["entity_id"]
    if typ == "reliability":
        expected = snapshot["windows"][id + "d"]
        for key in [
            "attempts",
            "transactions",
            "attempt_success_rate",
            "failure_rate",
            "pending_rate",
            "timeout_rate",
            "completion_rate",
            "refund_reversal_rate",
            "retry_rate",
        ]:
            actual = row.get(key)
            value = expected[key]
            assert (
                actual is None
                and value is None
                or actual is not None
                and value is not None
                and abs(actual - value) < 1e-9
            ), (key, actual, value)
            checks += 1
    else:
        expected = next(
            r
            for r in snapshot["customers" if typ == "customer" else "merchants"]
            if r["id"] == id
        )["behavior"]
        for key in [
            "frequency_30d",
            "frequency_previous_30d",
            "monetary_minor_30d",
            "active_days_30d",
        ]:
            assert abs(row[key] - expected[key]) < 1e-9, (
                id,
                key,
                row[key],
                expected[key],
            )
            checks += 1
print(
    json.dumps(
        {
            "run_id": result["run"]["id"],
            "checks_passed": checks,
            "input_rows": result["run"]["input_rows"],
            "status": "passed",
        },
        indent=2,
    )
)
