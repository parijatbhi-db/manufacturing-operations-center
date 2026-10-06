# Manufacturing Operations Center

Combined repo for the **Manufacturing Operations Center** demo: a Databricks App that surfaces an AI/BI dashboard, a Lakebase-backed wafer operations desk, a Genie space, a Multi-Agent Supervisor and the Lakeflow refresh pipeline for KARI Semiconductor STDF test quality.

**Live app:** https://mfg-ops-center-1444828305810485.aws.databricksapps.com
**Workspace:** `e2-demo-field-eng.cloud.databricks.com` (org `1444828305810485`)

## Repo layout

```
.
├── app/        # Databricks App (React + Vite + Node.js/Express)
└── bundle/     # Databricks Asset Bundle - data generation, Lakeflow ingest pipeline,
                #   transformations, Lakebase project + synced tables, dashboard,
                #   Genie space, and Agent Bricks (KA + MAS) sync
```

## What the demo does

CPO at KARI Semiconductor Ops needs to ingest STDF wafer-sort/final-test files, standardize them into analytics-ready schemas, and monitor yield, bin distributions, and tester utilization. The bundle lays down the data, dashboard, and agent infrastructure; the app gives Operations a single pane to query it.

| Surface | Asset | id |
|---|---|---|
| Dashboard | Semiconductor Test Quality and Throughput | `01f13ffb2116152b9c57017ac6989369` |
| Lakeflow pipeline | [mfg_ops] STDF raw ingestion (Lakeflow) | `bb686d6e-f137-4d1b-ae5d-924ac9b6629a` |
| Lakeflow Job | [mfg_ops] STDF pipeline - ingest, transform, serve | `848871296768056` |
| Lakebase project | Manufacturing Operations Center | `parijat-mfg-ops` (synced `mfg_ops.lb_*`, write-back `wafer_ops.wafer_dispositions`) |
| Genie space | STDF Semiconductor Test Quality Analytics | `01f1403396011f339af2cb207b69e9b6` |
| Knowledge Assistant | STDF-Test-Quality-KA | tile `b49c04e7-8a4b-4c5e-b7a9-6422165f5516`, endpoint `ka-b49c04e7-endpoint` |
| Multi-Agent Supervisor | Manufacturing-Operations-Supervisor | tile `40863b57-1699-430c-8335-ee6db4a0c691`, endpoint `mas-40863b57-endpoint` |

The MAS routes between the KA (definitions, change-log context) and the Genie space (live SQL on STDF gold tables).

## Docs

- [Design document](docs/DESIGN.md): architecture, data model, wafer-map classifier, decisions
- [Demo talk track](docs/TALK_TRACK.md): 20-minute script, Q&A, key numbers

## Deploy from scratch

### 1. Bundle — data, dashboard, agents

```bash
cd bundle
databricks bundle validate -p DEFAULT
databricks bundle deploy --force-lock -p DEFAULT
databricks bundle run demo_workflow -p DEFAULT
```

The bundle owns `parijat_demos.mfg_ops` end to end — it is the single source of truth for every downstream asset. The job runs:
1. `generate_data` — synthetic STDF wafer-sort sessions (complete die maps per wafer, with injected spatial patterns) → parquet files in the `raw_data` volume
2. `ingest_raw` — Lakeflow Declarative Pipeline: Auto Loader streams the files into `raw_stdf_*` streaming tables with data-quality expectations
3. `sql_transformations` — silver → gold (`transformations.sql`), wafer-map pattern classification (`gold_wafer_patterns`), and the `mv_stdf_*` metric views used by Genie
4. `refresh_lakebase` — snapshot-refreshes the Lakebase synced tables and grants the app's service principal read access
5. `export_kb_docs` — regenerates the KA corpus (data dictionary + gold CSVs) in `/Volumes/parijat_demos/mfg_ops/raw_data/docs`
6. `sync_agent_bricks` — points the KA at that docs folder, re-syncs it, and creates the MAS if missing (`redeploy_agents.py`)

The job has no schedule; run it from the app's **Data Pipeline** tab (Refresh data) or the Jobs UI.

The bundle also deploys the schema, volume, SQL warehouse (`parijat-mfg-ops`), Lakeflow pipeline, Lakebase project + synced tables, Lakeview dashboard (incl. the **Wafer Map Patterns** page) and the Genie space (`src/stdf_genie.geniespace.json`). It uses the direct deployment engine.

**Fresh workspace bootstrap:** the Genie space can't be created until the metric views exist, so on the very first deploy replace `${resources.genie_spaces.stdf_genie.id}` in `databricks.yml` with a placeholder, comment out `include: resources/*.yml`, deploy + run the job, then restore both and deploy again.

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
