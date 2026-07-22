"""
CareFlow — Layer 1: Synthetic Data Generator
Generates 200 realistic software customers with varied health patterns.
"""

import random
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

fake = Faker()
random.seed(42)
np.random.seed(42)

DB_PATH = Path(__file__).parent / "customers.db"
CSV_PATH = Path(__file__).parent / "customers.csv"

ORG_TYPES = ["NGO", "government", "enterprise"]

ALL_FEATURES = [
    "dashboard", "reporting", "api_integration", "user_management",
    "analytics", "export", "notifications", "sso", "audit_log", "custom_fields",
]

MONTHLY_MULTIPLIERS = {
    1: 1.10, 2: 1.05, 3: 1.15, 4: 1.00, 5: 1.05, 6: 0.95,
    7: 0.90, 8: 0.85, 9: 1.10, 10: 1.05, 11: 1.00, 12: 0.80,
}

ARCHETYPES = {
    "healthy":  {"weight": 0.35, "login_range": (8, 20),  "onboarding": (75, 100), "features": (5, 10), "tickets": (0, 2)},
    "at_risk":  {"weight": 0.25, "login_range": (2, 7),   "onboarding": (40, 74),  "features": (2, 5),  "tickets": (2, 5)},
    "critical": {"weight": 0.20, "login_range": (0, 2),   "onboarding": (0, 39),   "features": (0, 2),  "tickets": (5, 12)},
    "new":      {"weight": 0.10, "login_range": (3, 10),  "onboarding": (10, 60),  "features": (1, 4),  "tickets": (0, 3)},
    "champion": {"weight": 0.10, "login_range": (15, 25), "onboarding": (95, 100), "features": (7, 10), "tickets": (0, 1)},
}


def _pick_archetype():
    archetypes = list(ARCHETYPES.keys())
    weights = [ARCHETYPES[a]["weight"] for a in archetypes]
    return random.choices(archetypes, weights=weights, k=1)[0]


def _signup_date(archetype):
    today = datetime.now()
    if archetype == "new":
        days_ago = random.randint(1, 30)
    elif archetype == "champion":
        days_ago = random.randint(180, 720)
    else:
        days_ago = random.randint(30, 540)
    return today - timedelta(days=days_ago)


def _last_login(archetype, signup):
    today = datetime.now()
    if archetype == "critical":
        days_ago = random.randint(14, 90)
    elif archetype == "at_risk":
        days_ago = random.randint(5, 20)
    elif archetype == "new":
        days_ago = random.randint(0, 5)
    else:
        days_ago = random.randint(0, 4)
    last = today - timedelta(days=days_ago)
    return max(last, signup + timedelta(hours=1))


def _apply_seasonal(base_logins):
    month = datetime.now().month
    multiplier = MONTHLY_MULTIPLIERS.get(month, 1.0)
    adjusted = int(base_logins * multiplier)
    return max(0, adjusted + random.randint(-1, 1))


def generate_customer(customer_id):
    archetype = _pick_archetype()
    cfg = ARCHETYPES[archetype]

    org_type = random.choice(ORG_TYPES)
    signup = _signup_date(archetype)
    last_login = _last_login(archetype, signup)

    base_logins = random.randint(*cfg["login_range"])
    login_freq = _apply_seasonal(base_logins)

    n_features = random.randint(*cfg["features"])
    features_used = random.sample(ALL_FEATURES, min(n_features, len(ALL_FEATURES)))

    onboarding_pct = random.randint(*cfg["onboarding"])
    tickets = random.randint(*cfg["tickets"])

    if org_type == "enterprise":
        contract_value = random.randint(10_000, 120_000)
    elif org_type == "government":
        contract_value = random.randint(5_000, 60_000)
    else:
        contract_value = random.randint(1_000, 15_000)

    return {
        "customer_id": f"CUST_{customer_id:04d}",
        "organization_name": fake.company(),
        "organization_type": org_type,
        "signup_date": signup.strftime("%Y-%m-%d"),
        "last_login_date": last_login.strftime("%Y-%m-%d"),
        "login_frequency_weekly": login_freq,
        "features_used": "|".join(features_used),
        "features_used_count": len(features_used),
        "onboarding_completion_percentage": onboarding_pct,
        "support_tickets_raised": tickets,
        "contract_value_usd": contract_value,
        "archetype": archetype,
    }


def generate_dataset(n=200):
    logger.info(f"Generating {n} customers...")
    records = [generate_customer(i + 1) for i in range(n)]
    df = pd.DataFrame(records)
    logger.info(f"Archetypes: {df['archetype'].value_counts().to_dict()}")
    return df


def save_to_csv(df):
    df.to_csv(CSV_PATH, index=False)
    logger.info(f"CSV saved -> {CSV_PATH}")


def save_to_sqlite(df):
    conn = sqlite3.connect(DB_PATH)
    df.to_sql("customers", conn, if_exists="replace", index=False)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_score_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL,
            score       REAL NOT NULL,
            category    TEXT NOT NULL,
            explanation TEXT,
            scored_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS churn_predictions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id      TEXT NOT NULL,
            churn_probability REAL NOT NULL,
            risk_label       TEXT NOT NULL,
            shap_explanation TEXT,
            predicted_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL,
            email_type  TEXT NOT NULL,
            subject     TEXT,
            body        TEXT,
            sent_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_reports (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            report_text TEXT NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    logger.info(f"SQLite saved -> {DB_PATH}")


def inject_anomaly(customer_ids, anomaly_type="inactive"):
    """
    Deliberately make specific customers go inactive or critical.
    anomaly_type: 'inactive' | 'at_risk' | 'critical'
    """
    conn = sqlite3.connect(DB_PATH)

    for cid in customer_ids:
        if anomaly_type == "inactive":
            new_last_login = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            conn.execute(
                "UPDATE customers SET last_login_date=?, login_frequency_weekly=0 WHERE customer_id=?",
                (new_last_login, cid)
            )
        elif anomaly_type == "at_risk":
            conn.execute(
                "UPDATE customers SET login_frequency_weekly=2, onboarding_completion_percentage=35, support_tickets_raised=4 WHERE customer_id=?",
                (cid,)
            )
        elif anomaly_type == "critical":
            new_last_login = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
            conn.execute(
                "UPDATE customers SET last_login_date=?, login_frequency_weekly=0, onboarding_completion_percentage=10, support_tickets_raised=10 WHERE customer_id=?",
                (new_last_login, cid)
            )
        logger.info(f"Anomaly '{anomaly_type}' injected into {cid}")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    df = generate_dataset(200)
    save_to_csv(df)
    save_to_sqlite(df)

    print("\n── Sample (5 rows) ─────────────────────────────────────────────────")
    print(df[["customer_id", "organization_name", "organization_type",
              "login_frequency_weekly", "onboarding_completion_percentage",
              "support_tickets_raised", "contract_value_usd", "archetype"]].head().to_string())

    print("\n── Archetype distribution ──────────────────────────────────────────")
    print(df["archetype"].value_counts().to_string())

    print("\n── Contract value by org type ──────────────────────────────────────")
    print(df.groupby("organization_type")["contract_value_usd"].describe()[["mean","min","max"]].round(0).to_string())

    print(f"\n  customers.csv  ->  {CSV_PATH}")
    print(f"  customers.db   ->  {DB_PATH}")
