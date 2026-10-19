# Verification record — 17 October 2026

## Completed

- **62 isolated regression tests pass.** Coverage includes the original acceptance scenarios and payment conflicts, late confirmations/receipts, future-feature exclusion, role boundaries and credential revocation, evidence races, preference/complaint/holdout suppression, incident resolution, partial/full refunds, uncertain sends, monthly cadence, absolute merchant impact and model promotion.
- **Scala 2.13 / Apache Spark 4.0.3 compiled and ran.** A native local[2] run processed 1,006,150 replicated immutable events into 963,200 attempts and 939,000 transactions. Benchmark results remain separate from normal metrics. This is not a remote-cluster or production-throughput claim. See [scale manifest](spark-scale-verification.json).
- The normal v2 dataset contains 120 customers, 12 merchants, 16,282 logical transactions and 17,423 events. Salary-cycle, monthly-user, weekend, merchant-size, outage and post-failure engagement patterns are generated with seed 42. The original v1 and legacy customer databases remain separate.
- **555 cross-engine assertions pass** on customer/merchant frequency, value, active days and 1/7/30-day reliability. Spark approximate p95 is excluded from exact equality. A second normal batch was launched through the signed-in dashboard and visibly completed. See [Spark verification](spark-verification.json).
- **All six n8n exports imported and executed successfully** with n8n 2.43.5 / Node 24 and a restricted automation credential. The approved-retention workflow executed action 15 and processed the outbox to simulated. Other runs correctly returned no new work where incidents/actions were already handled. Schedules remain inactive. See [node-level results](n8n-verification.json).
- **Live Redis 7.4.9** cache miss/write/hit/30-second TTL verified, including database-specific keys. SQLite fallback is covered by regression tests. See [Redis verification](redis-verification.json).
- Both model pipelines trained and wrote FINISHED MLflow runs. A separate retraining candidate completed, passed both baseline gates, was promoted through the atomic pointer, and both models loaded successfully. See [governance verification](governance-verification.json).
- Authenticated API smoke checks passed for original and wallet routes, operations, Spark, monitoring and SHAP prediction. See [API verification](api-verification.json).
- Browser verification covered operator login, executive reporting, incident owner/note update, Spark engagement tables and successful dashboard batch submission. No browser errors/warnings were recorded. Screenshots are included.
- Python compilation, Ruff undefined/unused-name checks and browser JavaScript syntax checks passed. The preserved Streamlit dashboard was previously checked using AppTest without exceptions; upstream deprecation warnings remain.

## Measured model results — synthetic data only

| Model | Selected algorithm | Test rows | PR-AUC | Prevalence baseline | Brier | Precision / recall at calibration-selected review threshold |
|---|---|---:|---:|---:|---:|---|
| 30-day inactivity | XGBoost | 119 | 0.376 | 0.202 | 0.150 | 0.318 / 0.875 |
| First terminal payment failure | XGBoost | 2,570 | 0.210 | 0.113 | 0.095 | 0.198 / 0.710 |

Both models have zero recall at 0.5 on this test set. The review thresholds were selected on calibration data, separately from candidate selection and final test. These weak-to-moderate synthetic results are not a deployment recommendation. Business action thresholds remain explicit experimental rules. See [complete evaluation](model-evaluation.json) for dates, candidates, features, calibration and limitations.

## Environment and external boundaries

Python 3.14 on macOS arm64; Java 17; Spark 4.0.3; Scala 2.13.16. Native API, SQLite, Redis, MLflow, n8n and Spark were exercised. Direct Python versions are recorded in `requirements-wallet-tested.txt`; transitive dependencies are not fully locked.

Docker Compose build/startup subsequently passed on 18 October; see the addendum below. A remote Spark cluster was not available; the batch output/export paths require shared-storage configuration there. Spark and retraining now use a persistent single-host job queue and a separate supervised worker; this is not a distributed scheduler.

SMTP/HTTPS adapter behavior was mock-tested, including provider acceptance versus delivered receipts and ambiguous failure handling. No real provider messages were sent. Live SMTP/webhook, legacy Slack, Google Sheets and Groq access require real credentials and verified recipients and remain unverified. Synthetic experiments cannot establish real retention uplift. Refund/settlement servicing records do not execute money movement.

Deployment follow-up: Compose syntax validates, and named volumes now isolate container tracking paths from native MLflow paths. Health checks gate startup. The read-only preflight reports are in `deployment-preflight.json` and `native-preflight.json`; container build/start remains blocked by an unresponsive engine and low disk space. Four additional regression tests cover preflight behavior.

## Docker verification — 18 October 2026

After the engine recovered and disk space was available, the image built and API/Redis became healthy; n8n started and its health endpoint returned 200. All 57 tests passed inside Python 3.12/Linux arm64. Both models trained with MLflow. API smoke checks and a Scala/Spark run over 17,441 events passed, including 555 independent metric comparisons. CareFlow is served on localhost:18000 and n8n on localhost:15678 to avoid port conflicts. This supersedes the earlier container-launch blocker. Native and container datasets remain separate. See `docker-verification.json`.


## Durable worker addendum — 19 October 2026

The updated suite has 62 passing tests both natively and in Docker, including atomic claims, persistent queue deduplication, stale-owner fencing, lease recovery, bounded retries and API permissions. A real API Spark request remained queued across an API restart and completed after the worker started. The published Spark output passed 555 metric comparisons. Worker attempts retain logs/results and require reviewed retries after interruption. See `worker-verification.json` for execution evidence.
