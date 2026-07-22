"""
CareFlow — FastAPI main application
Exposes endpoints for all layers.
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

DB_PATH = Path(__file__).parent.parent / "data" / "customers.db"

app = FastAPI(title="CareFlow API", version="1.0.0")

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_conn():
    return sqlite3.connect(DB_PATH)


# ── Layer 2: Health Score Endpoints ──────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def homepage(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard.html")


@app.get("/health")
def api_health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/customers")
def get_customers():
    conn = get_conn()
    cursor = conn.execute("SELECT * FROM customers ORDER BY organization_name ASC")
    rows = cursor.fetchall()
    columns = [column[1] for column in conn.execute("PRAGMA table_info(customers)").fetchall()]
    conn.close()
    data = [dict(zip(columns, row)) for row in rows]
    return {"data": data, "count": len(data)}


@app.get("/scores")
def get_all_scores():
    """Return latest health score for every customer."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT h.customer_id, c.organization_name, c.organization_type,
               c.contract_value_usd, h.score, h.category, h.explanation, h.scored_at,
               c.last_login_date
        FROM health_score_history h
        JOIN customers c ON c.customer_id = h.customer_id
        WHERE h.scored_at = (
            SELECT MAX(scored_at) FROM health_score_history
            WHERE customer_id = h.customer_id
        )
        ORDER BY h.score ASC
    """).fetchall()
    conn.close()

    res = []
    for r in rows:
        try:
            last_login = datetime.strptime(str(r[8]), "%Y-%m-%d")
            days_inactive = (datetime.now() - last_login).days
        except Exception:
            days_inactive = 0
        res.append({
            "customer_id":        r[0],
            "organization_name":  r[1],
            "organization_type":  r[2],
            "contract_value_usd": r[3],
            "health_score":       r[4],
            "category":           r[5],
            "explanation":        r[6],
            "scored_at":          r[7],
            "last_login_date":    r[8],
            "days_since_login":   days_inactive,
        })
    return {"data": res, "count": len(res)}


@app.get("/scores/{customer_id}")
def get_score(customer_id: str):
    """Return latest health score for a single customer."""
    conn = get_conn()
    row = conn.execute("""
        SELECT h.customer_id, c.organization_name, c.organization_type,
               c.contract_value_usd, h.score, h.category, h.explanation, h.scored_at,
               c.last_login_date
        FROM health_score_history h
        JOIN customers c ON c.customer_id = h.customer_id
        WHERE h.customer_id = ?
        ORDER BY h.scored_at DESC LIMIT 1
    """, (customer_id,)).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail=f"Customer {customer_id} not found")

    try:
        last_login = datetime.strptime(str(row[8]), "%Y-%m-%d")
        days_inactive = (datetime.now() - last_login).days
    except Exception:
        days_inactive = 0

    return {
        "customer_id":        row[0],
        "organization_name":  row[1],
        "organization_type":  row[2],
        "contract_value_usd": row[3],
        "health_score":       row[4],
        "category":           row[5],
        "explanation":        row[6],
        "scored_at":          row[7],
        "last_login_date":    row[8],
        "days_since_login":   days_inactive,
    }


@app.get("/scores/category/{category}")
def get_scores_by_category(category: str):
    """Return all customers in a given category: Healthy | At Risk | Critical"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT h.customer_id, c.organization_name, c.organization_type,
               c.contract_value_usd, h.score, h.category, h.explanation, h.scored_at,
               c.last_login_date
        FROM health_score_history h
        JOIN customers c ON c.customer_id = h.customer_id
        WHERE h.category = ?
          AND h.scored_at = (
              SELECT MAX(scored_at) FROM health_score_history
              WHERE customer_id = h.customer_id
          )
        ORDER BY h.score ASC
    """, (category,)).fetchall()
    conn.close()

    res = []
    for r in rows:
        try:
            last_login = datetime.strptime(str(r[8]), "%Y-%m-%d")
            days_inactive = (datetime.now() - last_login).days
        except Exception:
            days_inactive = 0
        res.append({
            "customer_id":        r[0],
            "organization_name":  r[1],
            "organization_type":  r[2],
            "contract_value_usd": r[3],
            "health_score":       r[4],
            "category":           r[5],
            "explanation":        r[6],
            "scored_at":          r[7],
            "last_login_date":    r[8],
            "days_since_login":   days_inactive,
        })
    return {"data": res, "count": len(res), "category": category}


@app.post("/scores/run")
def trigger_scoring():
    """Manually trigger a full scoring run."""
    from scoring.health_score import run_scoring
    scores = run_scoring()
    dist = scores["category"].value_counts().to_dict()
    return {"message": "Scoring complete", "distribution": dist, "total": len(scores)}


# ── Layer 3: Churn Prediction Endpoints (wired in Layer 3) ───────────────────

@app.get("/churn")
def get_churn_predictions():
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.customer_id, c.organization_name, c.organization_type,
               c.contract_value_usd, p.churn_probability, p.risk_label,
               p.shap_explanation, p.predicted_at
        FROM churn_predictions p
        JOIN customers c ON c.customer_id = p.customer_id
        WHERE p.predicted_at = (
            SELECT MAX(predicted_at) FROM churn_predictions
            WHERE customer_id = p.customer_id
        )
        ORDER BY p.churn_probability DESC
    """).fetchall()
    if not rows:
        conn.close()
        run_churn_pipeline()
        conn = get_conn()
        rows = conn.execute("""
            SELECT p.customer_id, c.organization_name, c.organization_type,
                   c.contract_value_usd, p.churn_probability, p.risk_label,
                   p.shap_explanation, p.predicted_at
            FROM churn_predictions p
            JOIN customers c ON c.customer_id = p.customer_id
            WHERE p.predicted_at = (
                SELECT MAX(predicted_at) FROM churn_predictions
                WHERE customer_id = p.customer_id
            )
            ORDER BY p.churn_probability DESC
        """).fetchall()
    conn.close()

    data = [
        {
            "customer_id":        r[0],
            "organization_name":  r[1],
            "organization_type":  r[2],
            "contract_value_usd": r[3],
            "churn_probability":  r[4],
            "risk_label":         r[5],
            "shap_explanation":   r[6],
            "predicted_at":       r[7],
        }
        for r in rows
    ]
    return {"data": data, "count": len(data)}


@app.post("/churn/run")
def run_churn_pipeline():
    from ml.churn_model import train_model, predict_all, save_predictions
    from ml.shap_explainer import explain_all

    train_model()
    predictions = predict_all()
    explanations = explain_all()
    save_predictions(predictions, explanations)

    dist = predictions["risk_label"].value_counts().to_dict()
    return {"message": "Churn pipeline complete", "distribution": dist, "total": int(len(predictions))}


@app.get("/churn/{customer_id}")
def get_churn_for_customer(customer_id: str):
    conn = get_conn()
    row = conn.execute("""
        SELECT customer_id, churn_probability, risk_label, shap_explanation, predicted_at
        FROM churn_predictions
        WHERE customer_id = ?
        ORDER BY predicted_at DESC LIMIT 1
    """, (customer_id,)).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail=f"No prediction for {customer_id}")

    return {
        "customer_id":       row[0],
        "churn_probability": row[1],
        "risk_label":        row[2],
        "shap_explanation":  row[3],
        "predicted_at":      row[4],
    }


# ── Layer 5: Email endpoints (wired in Layer 5) ───────────────────────────────

class EmailRequest(BaseModel):
    customer_id: str
    email_type: str  # check_in | re_engagement | onboarding_welcome


@app.post("/email/generate")
def generate_email(req: EmailRequest):
    from llm.email_generator import generate_customer_email
    result = generate_customer_email(req.customer_id, req.email_type)
    return result


@app.get("/emails")
def get_email_log(customer_id: Optional[str] = None):
    conn = get_conn()
    if customer_id:
        rows = conn.execute(
            "SELECT * FROM email_log WHERE customer_id=? ORDER BY sent_at DESC",
            (customer_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM email_log ORDER BY sent_at DESC LIMIT 100"
        ).fetchall()
    conn.close()
    data = [{"id": r[0], "customer_id": r[1], "email_type": r[2],
             "subject": r[3], "body": r[4], "sent_at": r[5]} for r in rows]
    return {"data": data, "count": len(data)}


# ── Layer 7: Weekly report endpoint (wired in Layer 7) ───────────────────────

@app.post("/report/weekly/run")
def run_weekly_report():
    from reports.weekly_report import generate_weekly_report
    report = generate_weekly_report()
    return {"report": report, "generated_at": datetime.now().isoformat()}


@app.get("/report/weekly/latest")
def get_latest_weekly_report():
    conn = get_conn()
    row = conn.execute(
        "SELECT report_text, created_at FROM weekly_reports ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return {"report": "", "generated_at": None, "message": "No weekly report generated yet"}
    return {"report": row[0], "generated_at": row[1]}


@app.get("/report/weekly")
def get_weekly_report_latest_alias():
    return get_latest_weekly_report()
