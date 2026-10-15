> Historical README. Original accuracy, delivery and automation claims below are not validated production claims. See current README and WALLET.md.

# CareFlow — AI-Powered Customer Success Automation Platform

> Intelligent automation that monitors software customers, predicts churn, triggers personalized interventions, and delivers live dashboards + weekly AI reports — all without manual work.

Built as a portfolio project for a **Customer Experience Strategy and Analytics** role.

---

## What CareFlow does

| Capability | How |
|---|---|
| Monitors every customer account in real time | Health scoring engine (Layer 2) |
| Predicts churn before it happens | XGBoost + SHAP explainability (Layer 3) |
| Sends personalized emails automatically | Groq LLM + n8n workflows (Layers 4 & 5) |
| Logs everything to CRM | Google Sheets + SQLite (Layer 4) |
| Live dashboard | Streamlit (Layer 6) |
| Weekly AI executive report | Groq LLM → Email + Slack (Layer 7) |

---

## Architecture

```
careflow/
├── data/           Layer 1 — Synthetic data generator (200 customers, 5 archetypes)
├── scoring/        Layer 2 — Health scoring engine (0-100 score, logged to SQLite)
├── ml/             Layer 3 — XGBoost churn model + SHAP explanations
├── api/            FastAPI — exposes all layers as REST endpoints
├── automation/     Layer 4 — n8n workflow JSONs (5 automated workflows)
├── llm/            Layer 5 — Groq LLM email personalization (3 email types)
├── dashboard/      Layer 6 — Streamlit live dashboard
└── reports/        Layer 7 — Weekly AI report generator
```

---

## Quick Start

### 1. Clone and set up environment
```bash
git clone <your-repo>
cd careflow
cp .env.example .env
# Add your GROQ_API_KEY to .env
```

### 2. Run setup (installs packages, generates data, trains model, starts n8n)
```bash
bash setup.sh
```

### 3. Start the API
```bash
uvicorn api.main:app --reload --port 8000
# Docs at http://localhost:8000/docs
# Modern browser UI at http://localhost:8000
```

### 4. Start the dashboard
```bash
streamlit run dashboard/streamlit_app.py
```

### 5. Open n8n (automation workflows)
```
http://localhost:5678
Login: admin / careflow123
Import the 5 workflow JSONs from automation/n8n_workflows/
```

---

## Tech Stack
Python · FastAPI · XGBoost · SHAP · Groq LLM (Llama 3.3) · n8n · Streamlit · SQLite · Faker · Pandas

---

## Layer-by-Layer Summary

### Layer 1 — Synthetic Data Generator (`data/generator.py`)
200 customers across 5 archetypes (healthy/at_risk/critical/new/champion).
Seasonal activity patterns. Org types: NGO, government, enterprise.
`inject_anomaly()` function for controlled testing.

### Layer 2 — Health Scoring Engine (`scoring/health_score.py`)
Weighted formula: login 30% + onboarding 25% + features 25% + tickets 20%.
Categories: Healthy (75-100), At Risk (50-74), Critical (<50).
Score history logged to SQLite with timestamps. Plain English explanation per score.

### Layer 3 — Churn Prediction (`ml/churn_model.py` + `ml/shap_explainer.py`)
XGBoost classifier — 97% accuracy on test set, AUC 0.99.
SHAP explainability: every prediction has a plain English reason.
Example: "99% churn risk. Key drivers: login frequency is low (0 logins/week), no activity in 49 days, onboarding only 6% complete."
Auto-retrains when called with fresh data.

### Layer 4 — n8n Automation Workflows (`automation/n8n_workflows/`)
5 workflows imported into n8n via Docker:
- Daily health check → email customers with score < 60
- Daily inactivity check → re-engagement email for 7+ day inactive users
- New customer trigger → onboarding drip sequence by org type
- Daily churn check → CRM task in Google Sheets for high-risk accounts
- Monday 9am → weekly AI report via email + Slack

### Layer 5 — LLM Personalization Engine (`llm/email_generator.py`)
Groq API (Llama 3.3-70b) generates fully personalized emails.
3 types: check_in, re_engagement, onboarding_welcome.
Each email reads the customer's actual data — not a template.
All emails logged to SQLite and Google Sheets CRM.

### Layer 6 — Streamlit Dashboard (`dashboard/streamlit_app.py`)
Live views: health distribution · churn pipeline · onboarding funnel ·
top 10 at-risk accounts · email log · health score trend over time.
Reads directly from SQLite. Auto-refreshes every 30 seconds.

### Layer 7 — Weekly AI Report (`reports/weekly_report.py`)
Triggered every Monday 9am by n8n.
Reads full week's data, passes to Groq LLM, generates structured report:
What happened · Who is at risk · Recommended actions · What worked last week.
Delivered via email + Slack webhook. Saved to SQLite.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/scores` | All customer health scores |
| GET | `/scores/{id}` | Single customer score |
| GET | `/scores/category/{cat}` | Scores by category |
| POST | `/scores/run` | Trigger full scoring run |
| GET | `/churn` | All churn predictions |
| GET | `/churn/{id}` | Single customer prediction |
| POST | `/email/generate` | Generate personalized email |
| GET | `/emails` | Email log |
| GET | `/report/weekly` | Generate weekly report |

---

## CV Bullet Points

- Built CareFlow, an end-to-end AI customer success automation platform in Python, combining XGBoost churn prediction (97% accuracy), SHAP explainability, and Groq LLM to monitor 200+ customer accounts and generate personalized interventions with zero manual effort
- Designed a weighted health scoring engine classifying customers into Healthy/At Risk/Critical segments with plain English score explanations, exposed via FastAPI and visualized in a live Streamlit dashboard
- Engineered 5 n8n automation workflows that trigger personalized LLM-written emails, log interactions to a Google Sheets CRM, and deliver weekly AI executive reports to email and Slack every Monday
- Applied SHAP (SHapley Additive exPlanations) to produce per-customer churn narratives — e.g. "99% churn risk: login frequency dropped to 0, inactive 49 days, onboarding 6% complete" — making ML predictions actionable for non-technical stakeholders

---

## Screenshots
*(Add after running locally)*
- `screenshots/dashboard_overview.png`
- `screenshots/churn_predictions.png`
- `screenshots/email_sample.png`
- `screenshots/weekly_report.png`
