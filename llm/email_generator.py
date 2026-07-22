"""
CareFlow — Layer 5: LLM Email Personalization Engine
Uses Groq API to write fully personalized customer emails.
3 email types: check_in | re_engagement | onboarding_welcome
"""

import os
import time
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq, RateLimitError

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "customers.db"

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL  = "llama-3.3-70b-versatile"


def _fetch_customer_context(customer_id: str) -> dict:
    conn = sqlite3.connect(DB_PATH)

    customer = conn.execute(
        "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()

    if not customer:
        conn.close()
        raise ValueError(f"Customer {customer_id} not found")

    cols = [d[0] for d in conn.execute("SELECT * FROM customers LIMIT 0").description]
    customer = dict(zip(cols, customer))

    score_row = conn.execute("""
        SELECT score, category, explanation FROM health_score_history
        WHERE customer_id = ? ORDER BY scored_at DESC LIMIT 1
    """, (customer_id,)).fetchone()

    churn_row = conn.execute("""
        SELECT churn_probability, risk_label, shap_explanation FROM churn_predictions
        WHERE customer_id = ? ORDER BY predicted_at DESC LIMIT 1
    """, (customer_id,)).fetchone()

    conn.close()

    from datetime import datetime as dt
    try:
        last_login = dt.strptime(str(customer["last_login_date"]), "%Y-%m-%d")
        days_inactive = (dt.now() - last_login).days
    except Exception:
        days_inactive = 0

    all_features = ["dashboard","reporting","api_integration","user_management",
                    "analytics","export","notifications","sso","audit_log","custom_fields"]
    used = customer.get("features_used","").split("|") if customer.get("features_used") else []
    unused = [f for f in all_features if f not in used]

    return {
        "customer_id":        customer_id,
        "organization_name":  customer["organization_name"],
        "organization_type":  customer["organization_type"],
        "contract_value_usd": customer["contract_value_usd"],
        "days_inactive":      days_inactive,
        "features_used":      used,
        "features_unused":    unused,
        "onboarding_pct":     customer["onboarding_completion_percentage"],
        "login_freq":         customer["login_frequency_weekly"],
        "health_score":       score_row[0] if score_row else "N/A",
        "health_category":    score_row[1] if score_row else "N/A",
        "health_explanation": score_row[2] if score_row else "",
        "churn_probability":  f"{churn_row[0]:.0%}" if churn_row else "N/A",
        "churn_risk_label":   churn_row[1] if churn_row else "N/A",
        "shap_explanation":   churn_row[2] if churn_row else "",
    }


def _build_prompt(ctx: dict, email_type: str) -> str:
    base = f"""
You are a senior customer success manager at a software company.
Write a genuine, human-feeling email (not a template) to this customer.

Customer context:
- Organization: {ctx['organization_name']} ({ctx['organization_type']})
- Contract value: ${ctx['contract_value_usd']:,}
- Health score: {ctx['health_score']}/100 ({ctx['health_category']})
- Days since last login: {ctx['days_inactive']}
- Onboarding: {ctx['onboarding_pct']}% complete
- Features they use: {', '.join(ctx['features_used']) or 'none'}
- Features they haven't tried: {', '.join(ctx['features_unused'][:3]) or 'none'}
- Churn risk: {ctx['churn_risk_label']} ({ctx['churn_probability']})
- Key issues: {ctx['health_explanation']}
"""

    if email_type == "check_in":
        prompt = base + """
Write a friendly check-in email. Tone: warm, professional, genuinely helpful.
Reference their specific situation. Mention 1-2 features they haven't tried that would help them.
Do NOT sound like a sales email. Sound like a real person who cares.
Output format — Subject: [subject line]\\n\\n[email body]
"""
    elif email_type == "re_engagement":
        prompt = base + """
Write a re-engagement email for a customer who has been inactive.
Acknowledge the gap without being pushy. Offer something specific and valuable.
Tone: casual, understanding, no guilt-tripping.
Output format — Subject: [subject line]\\n\\n[email body]
"""
    elif email_type == "onboarding_welcome":
        prompt = base + f"""
Write an onboarding welcome email for a new {ctx['organization_type']} customer.
Make it specific to their org type. Give 3 concrete first steps.
Tone: excited, welcoming, clear.
Output format — Subject: [subject line]\\n\\n[email body]
"""
    else:
        raise ValueError(f"Unknown email_type: {email_type}")

    return prompt


def _parse_email(raw: str) -> tuple[str, str]:
    lines = raw.strip().split("\n")
    subject = ""
    body_lines = []
    in_body = False
    for line in lines:
        if line.lower().startswith("subject:"):
            subject = line[8:].strip()
        elif subject and not in_body:
            in_body = True
            if line.strip():
                body_lines.append(line)
        else:
            body_lines.append(line)
    return subject, "\n".join(body_lines).strip()


def _save_email(customer_id: str, email_type: str, subject: str, body: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO email_log (customer_id, email_type, subject, body, sent_at) VALUES (?,?,?,?,?)",
        (customer_id, email_type, subject, body, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()


def generate_customer_email(customer_id: str, email_type: str) -> dict:
    ctx = _fetch_customer_context(customer_id)
    prompt = _build_prompt(ctx, email_type)

    # Retry with backoff for Groq rate limits (free tier: 12k TPM)
    max_retries = 3
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
                temperature=0.7,
            )
            break
        except RateLimitError as e:
            if attempt < max_retries:
                wait = 3 * (2 ** attempt)  # 3s, 6s, 12s
                logger.warning(f"Rate limited, retrying in {wait}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
            else:
                logger.error(f"Rate limit exceeded after {max_retries} retries")
                raise

    raw = response.choices[0].message.content
    subject, body = _parse_email(raw)

    _save_email(customer_id, email_type, subject, body)
    logger.info(f"Email generated: {email_type} for {customer_id}")

    return {
        "customer_id":   customer_id,
        "email_type":    email_type,
        "subject":       subject,
        "body":          body,
        "organization":  ctx["organization_name"],
        "health_score":  ctx["health_score"],
        "churn_risk":    ctx["churn_risk_label"],
    }


if __name__ == "__main__":
    import sys
    cid = sys.argv[1] if len(sys.argv) > 1 else "CUST_0001"
    for etype in ["check_in", "re_engagement", "onboarding_welcome"]:
        print(f"\n{'='*60}")
        print(f"Email type: {etype} | Customer: {cid}")
        print('='*60)
        result = generate_customer_email(cid, etype)
        print(f"Subject: {result['subject']}\n")
        print(result['body'])
