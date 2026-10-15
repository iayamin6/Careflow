# Wallet n8n workflows

Six inactive workflow exports extend the five original workflows, which remain in `../n8n_workflows/`.

| Export | Trigger (Asia/Dhaka) | Behavior |
|---|---|---|
| 01 | Daily 08:00 | Recompute decisions and model-supported evidence |
| 02 | Daily 08:15 | Record high-priority customer investigations in dry-run execution history |
| 03 | Every 30 minutes | Refresh and record payment investigations |
| 04 | Hourly | Refresh and record shared merchant investigations |
| 05 | Daily 08:30 | Execute approved, currently eligible retention actions in dry-run mode |
| 06 | Monday 09:00 | Fetch structured operational summary |

All include a manual trigger for testing. URLs default to `http://careflow:8000/wallet`. This hostname is the API service in Compose. For native n8n, replace the hostname with `127.0.0.1`; change the port if your API uses a different one. Each request has a 120-second timeout. Imports do not enable schedules.

1. Start the API and create the synthetic wallet dataset.
2. Import the six JSON files in n8n (or use the server CLI `import:workflow --separate --input=...`).
3. Verify the API hostname/port in HTTP Request nodes.
4. Run manually. Inspect both n8n execution output and `/wallet/automation`.
5. For workflow 05, approve an eligible action in the dashboard first. Consent and fresh issue checks still apply at execution.
6. Activate schedules only after manual execution works. The original outreach workflows bypass the wallet decision service; keep them inactive for the wallet demo.

Execution keys include workflow ID, action ID and attempt number. HTTP retransmission reuses the same key; backend retries increment attempts and enforce backoff. All failures remain visible as failed requests/executions. A stale recommendation is rejected rather than silently dispatched. Operational action completion means the dry-run alert was recorded, not that a payment problem was resolved.

No credential placeholders are required for these dry-run workflows. Real delivery requires a separately implemented and verified provider adapter, verified destinations, authenticated operator approval, receipt reconciliation and consent handling; it is outside this synthetic demonstration.

Reference: [n8n server CLI documentation](https://docs.n8n.io/hosting/cli-commands/).
