# Requirement coverage — 19 October 2026

The master prompt describes an independent portfolio simulation. “Implemented” below means working application code with synthetic/local verification, not certification or a live financial-service deployment.

| Requirement | Implementation and evidence |
|---|---|
| Preserve original CareFlow | Original customer DB, FastAPI routes, Streamlit dashboard, Groq modules and five workflow exports retained; business API access now requires an operator token |
| Payment reliability | Attempt and logical transaction reconciliation; success/failure/pending/timeout/retry/refund rates, latency, channel and 1/7/30-day views |
| Customer behavior | RFM, active days, merchant diversity, overlapping segments, relative health, cadence and calendar baselines, high-value/low-frequency handling |
| Merchant behavior | Own-history reliability and volume baselines, size/category peer context, unique/returning customers, peak hour, shared incidents and overdue settlements |
| Churn and payment ML | XGBoost/LightGBM candidates; temporal selection, calibration and test; PR-AUC/prevalence baselines; SHAP; MLflow artifacts |
| Experience/anomalies | Sample-aware reliability drops, observed unresolved payments, complaint categories, settlement delays and affected customers; no causal root-cause claim |
| Central decisions | Operational precedence, complaint routing, hypothetical payment review, onboarding, re-engagement, retention and confirmed-resolution follow-up |
| Human operations | Role-controlled approvals, dismissals with expiring overrides, incident owners/status/notes and actor-attributed audit |
| Automation A–F | Six inactive authenticated n8n exports; all imported and executed locally, including nonempty approved outreach and outbox simulation |
| Delivery safeguards | Idempotency, cooldown, bounded retry, consent/preferences, verified allowlist, receipt deduplication and accepted-versus-delivered distinction |
| Communication content | Evidence-constrained personalized templates; original optional Groq code retained; model cannot grant eligibility or compensation |
| Settlement/refund servicing | Amount/original-payment validation, settlement due/completion tracking, refund state machine and cumulative cap; full refunds update event state |
| Complaint extension | English keyword routing and heuristic sentiment; unresolved complaints take precedence over retention; confidence explicitly not calibrated |
| Model governance | Drift reports, stored predictions, matured-label evaluation, separate retraining candidates, baseline gates and atomic promotion |
| Outcomes/experiments | Observed before/after activity, deterministic control holdouts, matured arm comparison and approximate uncertainty; no demonstrated causal uplift |
| Scala + Spark addition | Compiled Scala 2.13 / Spark 4.0.3 job, bounded-memory export, distributed aggregations, Parquet and JSON, API/UI integration, failed-run history; million-event replicated local exercise |
| Redis | Live 30-second summary cache tested; SQLite fallback; cache partitioned by DB path; never used for action eligibility |
| Security/privacy | Synthetic data, hashed/expiring/revocable tokens, five roles, per-process request throttle, input validation, secret exclusion, protected legacy endpoints |
| Dashboard | Executive, reliability, customer, merchant, decision, model, automation, incident, servicing, preference, complaint, Spark, experiment, governance and outbox views |
| Reproducibility | Seeded seasonal/friction generator, separate DB/model paths, scripts, model reports, 62 regression tests, Spark comparisons and setup documentation |

## Explicit boundaries

- **External provider setup remains:** SMTP/HTTPS adapter code exists and is mock-tested. Actual email/webhook delivery, legacy Slack/Google Sheets/Groq access and provider receipt verification need real credentials and verified recipients. No real messages were sent.
- **Deployment verification remains:** native API, Redis, MLflow, n8n and local Spark were exercised. Docker build/startup, service health, container tests, model training and Spark/API checks subsequently passed. A remote Spark cluster remains unverified. Spark and retraining now use a persistent SQLite queue and a separate supervised worker; queued work survived an API restart in Docker. Remote hosting, TLS and real-provider checks still need deployment configuration.
- **Value analysis is a scenario:** annualized volume at an assumed margin is implemented. A trained lifetime-value model was not part of the defined core target and is not claimed.
- **No real financial execution:** settlement/refund entries record confirmed outcomes; there is no real wallet money-movement, fraud scoring, credit decision, compensation issuance or transaction-blocking adapter.
- **No production validity claim:** synthetic temporal metrics, heuristic NLP/calendar baselines, experimental policy thresholds and simulated experiments require real-world evaluation before operational use.

The regression tests exercise the requested acceptance scenarios: payment problems precede retention, shared merchant alerts aggregate customers, onboarding respects short tenure, recovered payments support a reviewed follow-up, low-risk stable activity needs no action, opt-out/cooldowns block contact, missing models fall back to rules, and repeated events/executions remain idempotent. Further cases cover late receipts, evidence races, sparse history, role separation, refunds, complaints, outbox uncertainty, holdouts and model promotion.
