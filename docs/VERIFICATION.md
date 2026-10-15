# Verification record — 15 October 2026

## Completed

- 27 isolated regression tests pass. The cases cover all eight requested acceptance scenarios at the service/API level, plus event conflicts, attempt identity, consent/approval, contact safeguards, concurrent duplicate execution, incoming-evidence races, stale pending payments, leakage prevention and six workflow graph definitions.
- Python modules compile and Ruff's undefined/unused-name checks pass for wallet services and tests. Browser JavaScript syntax checks pass.
- Original API endpoints remain accessible. The original Streamlit dashboard runs through Streamlit AppTest without exceptions (upstream deprecation warnings remain).
- Synthetic dataset generated: 120 customers, 12 merchants, 18,780 logical transactions and 20,123 immutable events, seed 42, cutoff `2026-10-14T20:24:22.123868+00:00`.
- Both model training pipelines ran. XGBoost was selected for churn; LightGBM for payment failure. Both MLflow runs are FINISHED in the local SQLite tracking store.
- Browser verification confirms the reliability view, model-monitoring table and pre-attempt prediction with signed SHAP contributions render. Browser approval and dry-run execution succeeded and the action changed to completed. Screenshots are included.
- Read-only API smoke checks cover original and wallet routes and a payment prediction with SHAP explanations. See `scripts/verify_demo.py`.

## Measured model results (synthetic only)

| Model | Final test rows | PR-AUC | Prevalence baseline PR-AUC | Brier |
|---|---:|---:|---:|---:|
| Churn / XGBoost | 119 | 0.493 | 0.210 | 0.136 |
| Payment failure / LightGBM | 2,819 | 0.215 | 0.118 | 0.098 |

Precision/recall at 0.5 is weak: churn recall 0.04, payment recall 0.0. These are honest demonstration results, not deployment recommendations. See `model-evaluation.json` for full metrics, candidate comparisons and exact time splits.

## Environment and remaining deployment checks

Python 3.14 on macOS arm64. Direct dependency versions are in `requirements-wallet-tested.txt`; transitive dependencies are not fully locked. Local SQLite and MLflow were exercised. Redis fallback was tested; the Redis server path and Docker Compose deployment have not yet been exercised. The Docker daemon is not running on this machine.

Live provider delivery is deliberately not implemented for fictional customers. The durable dry-run transport is tested; it cannot prove real email/Slack delivery or causal retention uplift. Original optional Groq, Google Sheets, SMTP and Slack integrations are preserved but were not invoked.

All six inactive exports imported and executed successfully using n8n 2.43.5 with Node.js 24.19.0. Native test copies used localhost port 8010. The merchant workflow recorded twelve dry-run executions; the approved-retention workflow was rerun with an approved action and recorded one execution. Workflows 2 and 3 correctly returned no individual actions while merchant incidents owned the alerts. See `n8n-verification.json` for node-level results. Schedules remain inactive. The Python task runner was unavailable, but these workflows use JavaScript and its runner registered successfully.
