# CareFlow — Digital Wallet Experience & Retention Decision System

CareFlow connects payment reliability, customer and merchant behavior, explainable machine learning, and appropriate support or retention actions. It extends the original customer-success application and preserves its database, API, Streamlit dashboard, Groq modules, and five original n8n exports.

**Portfolio simulation:** wallet data is synthetic. External delivery defaults to dry-run; financial service records do not move money. Model accuracy and benchmark results are demonstration measurements, not production claims.

## Run locally

Python 3.12–3.14, Java 17, and an isolated Python environment are recommended.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-wallet.txt
python -m wallet.synthetic
python -m wallet.models
mkdir -p .local
python -m wallet.security --name operator --role admin --output .local/operator.token
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Open [the wallet dashboard](http://127.0.0.1:8000/wallet) and sign in using the token saved in your private file. `/docs` has a Bearer **Authorize** control. `/` retains the original customer dashboard, using the same browser-session credential. Tokens expire, can be revoked, and are stored only as hashes in SQLite. Never commit token files.

The generator refuses to overwrite a populated database. Set `CAREFLOW_WALLET_DB` and `CAREFLOW_MODEL_DIR` to new paths to make a separate dataset/model set. The original `data/customers.db` is retained. Generation, training and dry-run operation need no external service credentials.

## Scala + Apache Spark

```sh
pip install pyspark==4.0.3
export SPARK_HOME="$(python -c 'import pathlib,pyspark; print(pathlib.Path(pyspark.__file__).parent)')"
scripts/build_spark.sh
python -m wallet.batch
python scripts/verify_spark.py
```

The Scala 2.13 job reconciles payment events, aggregates 1/7/30-day reliability, computes customer and merchant engagement metrics, and writes JSON and Parquet, including date-partitioned daily activity. The API publishes completed results atomically and shows their timestamp/freshness in **Spark analytics**, customer evidence and decision context. Live eligibility still checks current operational records.

`CAREFLOW_SPARK_MASTER` defaults to `local[2]`. The job uses Spark DataFrames and distributed aggregations; a remote cluster requires accessible input/output storage and deployment configuration. Local verification processed **1,006,150 replicated events**. This was a scale exercise on one computer, not a production or cluster throughput benchmark. `python -m wallet.batch --replicas 50` creates separately marked benchmark results that cannot replace normal analytics.

## Implemented features

- Immutable event ingestion, duplicate/conflict checks, retry/timeout recovery and separate attempt/transaction accounting.
- Reliability windows, RFM, overlapping customer segments, merchant baselines, calendar/cadence evidence, health scores and value scenarios.
- Payment-failure and 30-day inactivity models: XGBoost/LightGBM comparison, separate temporal selection/calibration/test sets, SHAP and MLflow tracking.
- Prioritized decisions, incident ownership and resolution notes, manual overrides, consent, category/channel preferences and human approval.
- Settlement tracking, refund lifecycle and reconciliation, lightweight complaint classification and support routing.
- Durable outbox with simulated delivery, configurable SMTP/HTTPS adapters, conservative retries and authenticated delivery receipts.
- Five operator roles, hashed expiring credentials, request limits and actor-attributed audit records.
- Model drift and matured-label monitoring, reviewable retraining candidates and atomic model promotion.
- Deterministic experiment holdouts, observed follow-up and matured outcome comparisons with uncertainty.
- Executive, reliability, customer, merchant, decision, operations, Spark, model and automation dashboard views.
- Six authenticated n8n workflows, optional Redis summary cache, and container configuration.

## Verification and documentation

```sh
python -m unittest discover -s tests -v
python scripts/verify_demo.py
mlflow ui --backend-store-uri sqlite:///data/mlflow.db --port 5000
```

See [verification results](docs/VERIFICATION.md), [architecture and metric definitions](docs/WALLET.md), [requirement coverage](docs/REQUIREMENTS.md), [n8n setup](automation/wallet_workflows/README.md), and the [historical README](docs/LEGACY_README.md).

## Configuration

| Variable | Responsibility |
|---|---|
| `CAREFLOW_WALLET_DB` | Durable wallet database; default `data/wallet.db` |
| `CAREFLOW_MODEL_DIR` | Trusted local artifacts; default `ml/wallet_models` |
| `MLFLOW_TRACKING_URI` | Defaults to SQLite tracking in `data/mlflow.db` |
| `REDIS_URL` | Optional 30-second summary cache; eligibility always reads SQLite |
| `SPARK_HOME` | Spark distribution including Scala compiler jars |
| `CAREFLOW_SPARK_MASTER` | Spark master, default `local[2]` |
| `CAREFLOW_BATCH_DIR` | Export/Parquet/run-log directory, default `data/batch` |
| `CAREFLOW_DELIVERY_MODE` | Default `dry_run`; `live` enables configured external adapters |
| `CAREFLOW_DELIVERY_ALLOWLIST` | Exact permitted destinations, comma-separated |
| `CAREFLOW_SMTP_HOST/PORT/FROM/USER/PASSWORD` | Optional TLS SMTP adapter configuration |
| `CAREFLOW_DELIVERY_WEBHOOK/TOKEN` | Optional HTTPS delivery adapter configuration |
| `GROQ_API_KEY` | Preserved original optional LLM integration |

Live delivery additionally requires an admin-verified destination, consent, preferences, approval and fresh eligibility. SMTP acceptance is distinct from confirmed delivery; ambiguous send outcomes require receipt/manual reconciliation. Keep credentials outside source control. Serve behind TLS and appropriate deployment controls before any non-local use; the included rate limiter is per-process, not a distributed abuse-prevention service.

## Containers

Run `python scripts/preflight.py --mode docker` first. See the [deployment guide](docs/DEPLOYMENT.md) for bootstrap, storage and readiness checks. `docker compose up --build` defines the API (including Java/Spark), Redis and n8n on loopback ports. Bootstrap data/models/token with `docker compose exec careflow` and the same Python commands above; import the workflows and configure the restricted credential. Container data, models, credentials and MLflow artifacts use named volumes, separate from native paths. Native and Docker services were tested. The container build, service health, 57 tests, model training, API smoke checks and Scala/Spark batch passed on 18 October 2026. See `docs/docker-verification.json`. Ports can be changed with `CAREFLOW_API_PORT` and `CAREFLOW_N8N_PORT`; verification used 18000 and 15678 to avoid an existing service.

![Spark engagement analytics](docs/screenshots/spark-analytics.png)
