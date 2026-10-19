"""
CareFlow — Layer 7: Weekly AI Report Generator
Reads past week's data and generates a structured plain English report via Groq.
Saves to SQLite. Triggered by n8n every Monday 9am.
"""

import os
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "customers.db"
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"


def _gather_weekly_data() -> dict:
    conn = sqlite3.connect(DB_PATH)
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    # New customers this week
    new_customers = conn.execute("""
        SELECT COUNT(*) FROM customers WHERE signup_date >= DATE('now', '-7 days')
    """).fetchone()[0]

    # Health score distribution now
    score_dist = conn.execute("""
        SELECT category, COUNT(*) as cnt
        FROM health_score_history h
        WHERE h.id = (SELECT id FROM health_score_history WHERE customer_id=h.customer_id ORDER BY scored_at DESC,id DESC LIMIT 1)
        GROUP BY category
    """).fetchall()

    # Critical accounts
    critical_accounts = conn.execute("""
        SELECT h.customer_id, c.organization_name, h.score, h.explanation
        FROM health_score_history h
        JOIN customers c ON c.customer_id = h.customer_id
        WHERE h.category = 'Critical'
          AND h.scored_at = (SELECT MAX(scored_at) FROM health_score_history WHERE customer_id = h.customer_id)
        ORDER BY h.score ASC LIMIT 5
    """).fetchall()

    # High churn risk
    high_churn = conn.execute("""
        SELECT p.customer_id, c.organization_name, p.churn_probability, p.shap_explanation
        FROM churn_predictions p
        JOIN customers c ON c.customer_id = p.customer_id
        WHERE p.risk_label = 'High Risk'
          AND p.predicted_at = (SELECT MAX(predicted_at) FROM churn_predictions WHERE customer_id = p.customer_id)
        ORDER BY p.churn_probability DESC LIMIT 5
    """).fetchall()

    # Email drafts generated this week
    emails_generated = conn.execute(
        """
        SELECT email_type, COUNT(*) FROM email_log
        WHERE sent_at >= ? GROUP BY email_type
    """,
        (week_ago,),
    ).fetchall()

    # Average health score
    avg_score = conn.execute("""
        SELECT AVG(score) FROM health_score_history h
        WHERE h.id = (SELECT id FROM health_score_history WHERE customer_id=h.customer_id ORDER BY scored_at DESC,id DESC LIMIT 1)
    """).fetchone()[0]

    conn.close()

    return {
        "new_customers": new_customers,
        "score_dist": {r[0]: r[1] for r in score_dist},
        "critical_accounts": critical_accounts,
        "high_churn": high_churn,
        "emails_generated": {r[0]: r[1] for r in emails_generated},
        "avg_health_score": round(avg_score, 1) if avg_score else 0,
    }


def generate_weekly_report() -> str:
    data = _gather_weekly_data()

    critical_text = (
        "\n".join(
            [
                f"  - {r[1]} (score {r[2]:.0f}): {r[3]}"
                for r in data["critical_accounts"]
            ]
        )
        or "  None"
    )

    churn_text = (
        "\n".join(
            [f"  - {r[1]}: {r[2]:.0%} churn risk — {r[3]}" for r in data["high_churn"]]
        )
        or "  None"
    )

    emails_text = (
        "\n".join(
            [
                f"  - {k}: {v} generated (delivery unverified)"
                for k, v in data["emails_generated"].items()
            ]
        )
        or "  None generated this week"
    )

    prompt = f"""
You are the head of customer success. Write a structured weekly report based on this data.

WEEKLY DATA SNAPSHOT:
- New customers onboarded: {data["new_customers"]}
- Average platform health score: {data["avg_health_score"]}/100
- Health distribution: {data["score_dist"]}
- Critical accounts (top 5):
{critical_text}
- High churn risk accounts (top 5):
{churn_text}
- Email drafts generated this week:
{emails_text}

Write a plain English report with these exact sections:
1. WHAT HAPPENED THIS WEEK (2-3 sentences summary)
2. WHO IS AT RISK AND WHY (specific accounts, specific reasons)
3. RECOMMENDED ACTIONS (3-5 concrete actions for the team)
4. OUTCOME LIMITATIONS (delivery and causal intervention effectiveness are not measured; do not infer success from drafts)

Be direct and specific. No fluff. Write like a real executive briefing.
"""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
        temperature=0.4,
    )

    report = response.choices[0].message.content

    # Save to DB
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO weekly_reports (report_text, created_at) VALUES (?, ?)",
        (report, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()

    logger.info("Weekly report generated and saved")
    return report


if __name__ == "__main__":
    print("Generating weekly report...\n")
    report = generate_weekly_report()
    print(report)
