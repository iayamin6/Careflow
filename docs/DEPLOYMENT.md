# Local deployment and preflight

Run `python scripts/preflight.py --mode docker` before building. It checks available disk space, Compose syntax and Docker's response with a 15-second timeout. It is read-only, prints no command output or credentials, and returns nonzero when a required check fails. Eight GiB is a practical build-headroom threshold, not a guarantee of image size.

The initial 17 October check found insufficient free space and an unresponsive Docker engine. After space was freed and Docker restarted, the build, service startup, 57 container tests, model training, API checks and Spark batch/comparison checks passed. See [Docker verification](docker-verification.json). The earlier [preflight](deployment-preflight.json) is retained as historical diagnostic evidence.

## Container storage

Compose uses named volumes for wallet data, models, MLflow metadata/artifacts, operator token files and n8n state. The API and Redis have health checks; n8n waits for a healthy API. Published ports remain bound to localhost.

The container dataset/model history is separate from native development. Mounting a native MLflow SQLite database directly into `/app/data` does not translate its absolute artifact paths into container paths. Fresh container training creates consistent `/app/...` artifact locations. Do not copy a native tracking DB as a migration strategy without migrating its artifacts and recorded locations too.

The original legacy customer dataset is included in the image and copied into the new data volume on its first creation. Native wallet databases, model artifacts, tracking logs and private tokens are excluded from the image. Existing named volumes persist during rebuilds; do not use `docker compose down --volumes` unless you intend to destroy that container data.

## Bootstrap after Docker and disk space are available

```sh
python scripts/preflight.py --mode docker
CAREFLOW_API_PORT=18000 CAREFLOW_N8N_PORT=15678 docker compose up --build -d
docker compose exec careflow python -m wallet.synthetic
docker compose exec careflow python -m wallet.models
docker compose exec careflow python -m wallet.security --name operator --role admin --output .local/operator.token
docker compose exec careflow python -m wallet.batch
docker compose exec careflow python scripts/verify_spark.py
docker compose exec careflow python scripts/preflight.py --url http://127.0.0.1:8000
```

The generator and token command refuse to overwrite existing data/files. On subsequent startups, omit bootstrap commands for data and credentials already created. Read your operator token locally from the private container file and use it at `http://127.0.0.1:18000/wallet`. Configure the separate automation-role credential in n8n as described in its [setup guide](../automation/wallet_workflows/README.md).

For native mode, set the intended `CAREFLOW_WALLET_DB`, `CAREFLOW_MODEL_DIR` and `SPARK_HOME`, then run `python scripts/preflight.py --url http://127.0.0.1:8010`. Missing data/models are warnings because bootstrap and rule-only operation are supported. This does not test model quality or unpickle model artifacts.

## External services

Delivery remains dry-run by default. Configure SMTP/HTTPS adapter settings only in a private environment, and use verified allowlisted recipients. Do not paste credentials into source files, workflow exports, issues or chat. Provider acceptance is not confirmed delivery; reconcile receipts or ambiguous outcomes. Existing Google Sheets/Groq/Slack integrations need their own real credentials and verification.

A remote Spark cluster needs shared input/output storage and deployment configuration. The verified job is local[2]. API background tasks are not a durable job queue; retain and reconcile interrupted batch/retraining runs before retrying. The current application does not move real money.

Host ports default to 8000/5678. The example uses 18000/15678 to avoid an existing local service; internal API requests and container workflow URLs still use port 8000.
