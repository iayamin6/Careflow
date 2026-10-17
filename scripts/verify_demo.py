"""Read-only smoke checks against the generated demo; no provider calls."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from api.main import app

from wallet.security import issue

client = TestClient(
    app, headers={"Authorization": "Bearer " + issue("smoke-test", "analyst", 1)}
)
results = {}
for path in [
    "/",
    "/health",
    "/customers",
    "/scores",
    "/churn",
    "/score-history",
    "/wallet",
    "/wallet/analytics",
    "/wallet/models",
    "/wallet/report",
    "/wallet/outcomes",
    "/wallet/incidents",
    "/wallet/servicing",
    "/wallet/complaints",
    "/wallet/batch",
    "/wallet/deliveries",
    "/wallet/experiments",
    "/wallet/models/candidates",
    "/wallet/models/monitoring",
]:
    response = client.get(path)
    assert response.status_code == 200, (path, response.text)
    results[path] = response.status_code
response = client.post(
    "/wallet/payment/predict",
    json={
        "customer_id": "W0001",
        "merchant_id": "M001",
        "amount_minor": 10000,
        "channel": "ussd",
    },
)
prediction = response.json()
assert prediction["status"] == "available" and prediction["drivers"], prediction
results["payment_model"] = {
    "status": prediction["status"],
    "has_shap_explanation": bool(prediction["drivers"]),
}
print(json.dumps(results, indent=2))
