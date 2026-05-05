# Manufacturing Operations Center

Combined repo for the **Manufacturing Operations Center** demo: a Databricks App that surfaces a Lakeview dashboard, a Genie space, and a Multi-Agent Supervisor for KARI Semiconductor STDF test quality.

**Live app:** https://mfg-ops-center-1444828305810485.aws.databricksapps.com
**Workspace:** `e2-demo-field-eng.cloud.databricks.com` (org `1444828305810485`)

## Repo layout

```
.
├── app/        # Databricks App (React + Vite + Node.js/Express)
└── bundle/     # Databricks Asset Bundle - data generation, transformations,
                #   dashboard, and Agent Bricks (KA + MAS) deploy
```

## What the demo does

CPO at KARI Semiconductor Ops needs to ingest STDF wafer-sort/final-test files, standardize them into analytics-ready schemas, and monitor yield, bin distributions, and tester utilization. The bundle lays down the data, dashboard, and agent infrastructure; the app gives Operations a single pane to query it.

| Surface | Asset | id |
|---|---|---|
| Dashboard | Semiconductor Test Quality and Throughput | `01f13ffb2116152b9c57017ac6989369` |
| Genie space | STDF Semiconductor Test Quality Analytics | `01f1403396011f339af2cb207b69e9b6` |
| Knowledge Assistant | STDF-Test-Quality-KA | tile `5578ac04-9888-47c0-8b14-ba483a0e0259`, endpoint `ka-5578ac04-endpoint` |
| Multi-Agent Supervisor | Manufacturing-Operations-Supervisor | tile `40863b57-1699-430c-8335-ee6db4a0c691`, endpoint `mas-40863b57-endpoint` |

The MAS routes between the KA (definitions, change-log context) and the Genie space (live SQL on STDF gold tables).

## Deploy from scratch

### 1. Bundle — data, dashboard, agents

```bash
cd bundle
databricks bundle validate -p DEFAULT
databricks bundle deploy --force-lock -p DEFAULT
databricks bundle run demo_workflow -p DEFAULT
```

This will:
1. Create the `parijat_demos.manufacturing` schema + `raw_data` volume in Unity Catalog
2. Generate synthetic STDF data via Faker
3. Run bronze → silver → gold transformations (`transformations.sql`)
4. Deploy the Lakeview dashboard
5. (If `bricks_conf.json` includes `knowledge_assistant` / `multi_agent_supervisor` blocks) deploy the agents via `deploy_resources.py`

The `redeploy_agents.py` script is a standalone fallback to redeploy just the KA + MAS without re-running the data pipeline. Submit it as a one-off Databricks job task with `databricks-sdk>=0.106.0` in the env spec.

### 2. App

```bash
cd app
# Frontend builds server-side on Databricks Apps; no local npm install needed.
databricks sync . /Workspace/Users/<you>/manufacturing-operations-center -p DEFAULT \
  --exclude node_modules --exclude client/node_modules --exclude client/dist --exclude .git
databricks apps deploy mfg-ops-center \
  --source-code-path /Workspace/Users/<you>/manufacturing-operations-center -p DEFAULT
```

App runs Express + React/Vite. On first visit, Databricks shows a one-time OAuth consent for the Genie + serving-endpoint scopes. After that, all three tabs work.

## App configuration

`app/app.yaml` declares two resources:
- `genie-space` → CAN_RUN on the Genie space
- `serving-endpoint` → CAN_QUERY on `mas-40863b57-endpoint` (the MAS)

Plus env-var defaults that let the React UI render workspace links to the Knowledge Assistant and Supervisor configure pages.

The dashboard is referenced via env var (Databricks Apps doesn't have a `dashboard` resource type — viewers need CAN_READ on the dashboard directly).

## See also

- `app/README.md` — App-only details (server routes, build pipeline, troubleshooting)
- `bundle/README.md` — Bundle origin (AI Demo Generator), schema, agent brick details
