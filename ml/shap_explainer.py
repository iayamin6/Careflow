"""
CareFlow — Layer 3: SHAP Explainability
Generates plain English churn explanations per customer.
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import shap

from ml.churn_model import load_model, _prep_features, FEATURES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "customers.db"

FEATURE_LABELS = {
    "login_frequency_weekly": "login frequency",
    "onboarding_completion_percentage": "onboarding completion",
    "features_used_count": "feature adoption",
    "support_tickets_raised": "support tickets",
    "days_since_login": "days since last login",
    "contract_value_usd": "contract value",
}


def _shap_to_text(
    shap_values: np.ndarray, feature_names: list, row: pd.Series, prob: float
) -> str:
    pairs = sorted(
        zip(shap_values, feature_names), key=lambda x: abs(x[0]), reverse=True
    )
    top = pairs[:3]

    reasons = []
    for val, feat in top:
        label = FEATURE_LABELS.get(feat, feat)
        actual = row[feat]

        direction = "increases" if val > 0 else "decreases"
        reasons.append(f"{label} = {actual:g} ({direction} model risk)")

    risk_pct = int(prob * 100)
    return f"{risk_pct}% churn risk. Key drivers: " + ", ".join(reasons) + "."


def explain_all() -> dict:
    """Returns dict of customer_id -> plain English SHAP explanation."""
    model = load_model()

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM customers", conn)
    conn.close()

    df = _prep_features(df)
    X = df[FEATURES]

    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)

    # Handle different SHAP versions / types (list, Explanation object, array)
    if isinstance(shap_values, list):
        shap_values_array = shap_values[1]
    elif hasattr(shap_values, "values"):
        shap_values_array = shap_values.values
        if len(shap_values_array.shape) == 3:
            shap_values_array = shap_values_array[:, :, 1]
    else:
        shap_values_array = shap_values
        if len(shap_values_array.shape) == 3:
            shap_values_array = shap_values_array[:, :, 1]

    probs = model.predict_proba(X)[:, 1]

    explanations = {}
    for i, row in df.iterrows():
        sv = shap_values_array[i]
        adjusted_p = float(probs[i])
        text = _shap_to_text(sv, FEATURES, row, adjusted_p)
        explanations[row["customer_id"]] = text

    logger.info(f"Generated SHAP explanations for {len(explanations)} customers")
    return explanations


if __name__ == "__main__":
    from ml.churn_model import train_model, predict_all, save_predictions

    print("Training model...")
    train_model()

    print("Generating predictions...")
    preds = predict_all()

    print("Generating SHAP explanations...")
    explanations = explain_all()

    print("Saving to DB...")
    save_predictions(preds, explanations)

    print("\n── High Risk Customers ─────────────────────────────────────────────")
    high_risk = preds[preds["risk_label"] == "High Risk"].head(5)
    for _, row in high_risk.iterrows():
        print(f"\n{row['customer_id']} — {row['organization_name']}")
        print(f"  Churn probability: {row['churn_probability']:.1%}")
        print(f"  Explanation: {explanations.get(row['customer_id'], 'N/A')}")

    print(f"\n── Risk distribution ───────────────────────────────────────────────")
    print(preds["risk_label"].value_counts().to_string())
