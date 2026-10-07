# Build narrative

This is how the Manufacturing Operations Center was actually built: which tools did what, the order things happened in, and the points where the plan changed. [docs/DESIGN.md](docs/DESIGN.md) describes the system as it is now. [evidence/](evidence/) proves that it runs. This file covers how it got there.

Sources: git history, the Claude Code session transcripts from 2026-09-29 to 2026-10-07, and the DESIGN.md gotchas list. Where something isn't recorded, this file says so rather than guessing.

---

## Tools used

| Tool | What it was used for |
|---|---|
| **Databricks AI Demo Generator** | Generated the starting bundle (`kari_semi_stdf`, a colleague's template from Oct 2025): synthetic STDF generator, SQL transforms, Lakeview dashboard, and `agent_bricks_service.py` / `redeploy_agents.py` for the KA and Supervisor. See [bundle/README.md](bundle/README.md). |
| **Claude Code** (Opus 5.5; Opus 4.8 for the 2026-09-29 redeploy session) | Did nearly all of the work after the fork: planning, code, bundle changes, deploys, debugging, docs. Commits are co-authored `Isaac <no-reply@databricks.com>`. |
| Claude Code **Databricks skills** | `databricks-ml-training` for the wafer models, `databricks-pipelines` before writing the Lakeflow pipeline, `databricks-lakebase` before the Lakebase project, synced tables and app client, `databricks-agent-bricks` for the KA/Supervisor questions, `databricks-apps` and `databricks-dabs` for the redeploy and resource bindings |
| **Databricks CLI** (v1.20, profile `e2-demo-fe`) | All deploys and checks: `bundle validate/deploy/run`, `bundle deployment migrate/bind/unbind`, `apps create-update`, `jobs`, `genie`, `lakeview publish`, raw `databricks api` calls |
| **Chrome DevTools MCP** | Ran the app locally to check that switching tabs keeps chat state and that the Back button works |
| **Web search** | Checked whether Agent Bricks or the KA had been renamed (they hadn't; see below) |
| `/code-review` | One review pass over the repo after the `mfg_ops` rework (2026-10-05) |
| Google Docs / Slides skills | Turned DESIGN.md and TALK_TRACK.md into Google Docs and built the business deck from the corporate template |

**Not recorded:** the React/Express app and the first version of the bundle arrived together in the first commit (`32ffd0b`, 2026-05-05). There's no session history on this machine from that period, so it isn't clear how the app shell was scaffolded. *(TODO, Parijat: fill this in.)*

---

## Timeline

### 0. Starting point (May 2026)
I forked the AI Demo Generator bundle and paired it with a three-tab app (Dashboard iframe, Genie proxy, Supervisor Agent proxy). The data lived in `parijat_demos.manufacturing`, mixed in with older ad-hoc STDF tables. The bundle's `deploy_agent_bricks` task failed on every run because it pinned `databricks-sdk>=0.18`, which is too old for the Knowledge Assistant API. The KA and Supervisor that actually worked were created by running `redeploy_agents.py` by hand with a newer SDK.

### 1. Redeploy and the 403 saga (2026-09-29)
I asked Claude Code to redeploy the repo to e2-demo-field-eng and confirm that everything worked.
- `app.yaml` still pointed at a KA (`5578ac04`) that no longer existed. It was patched to `b49c04e7`, but only in the local clone.
- **Question I raised:** "Instead of KA, is there a newer way? I believe Agent Bricks was rebranded." Claude Code loaded the `databricks-agent-bricks` skill and searched the web. Nothing had been renamed: KA had simply gone GA (2026-01-27), and Agent Bricks is still the umbrella. The app only calls the **Supervisor endpoint**, and the Supervisor routes to Genie and the KA, so I kept the managed tiles. I didn't move to a custom MLflow agent.
- **Genie and the Supervisor returned 403 errors.** Binding the `genie-space` and `serving-endpoint` app resources wasn't enough. The fix took four steps:
  1. Declare `user_api_scopes` with `apps create-update`.
  2. Restart the app.
  3. Revoke my old OAuth consent (`DELETE …/user-consent/me`), because Databricks doesn't ask existing users to consent again.
  4. Approve the new consent prompt.
- The dashboard embed kept using the old warehouse. The app iframe shows the *published* dashboard, whose warehouse is separate from the draft's, so the fix was `lakeview publish --warehouse-id`.

### 2. Planning against the challenge brief (2026-10-05)
I pasted the brief (Lakeflow → Unity Catalog → Lakebase → ML/GenAI → Genie → App, as one connected journey) and asked for a business problem and a plan. Claude Code analysed the repo and the separate STDF processing folder, then proposed:
- the problem: yield excursions caught days late, with root cause spread across testers, probe cards and firmware changes;
- a six-phase plan;
- a first pass at keeping chat state across tabs.

The plan ended with four decisions for me. My answers changed the plan in several places (next section).

### 3. Single source of truth and wafer-map patterns (2026-10-05, `af8a0dc`)
I asked for this: "STDF pipeline should be the single source of truth. New schema `parijat_demos.mfg_ops`. Add wafer-map pattern classification."
- The generator was rewritten to produce **whole wafers on circular die grids**, with WM-811K-style patterns injected and an Edge-Ring incident on testers TST-AUS-03..05 / probe card PC-AUS-447. The old version sampled random dies. The new one produces real wafer maps and a realistic UPH (~600, up from ~2).
- Transforms take catalog and schema as parameters (`USE CATALOG IDENTIFIER(:catalog)`), so the whole demo can be pointed elsewhere with one variable.
- The bundle now owns the Genie space (`genie_spaces` resource bound to the existing ID). The Terraform deployment engine can't bind Genie spaces, so I **migrated to the direct engine**. Before the migration would run, resource references had to become variable references, and a stale WAL file had to be removed.
- On a fresh deploy, Genie fails because the metric views don't exist yet. The workaround is a **two-pass bootstrap**: deploy with a literal Genie ID, run the job, then restore the reference (written up in DESIGN.md §8).
- `pb-demos` had been deleted, so the bundle now creates and binds its own warehouse, `parijat-mfg-ops`.
- `deploy_agent_bricks` was replaced with `export_kb_docs` (which generates the KA corpus from UC metadata) and `sync_agent_bricks` (`redeploy_agents.py` with a current SDK, taking `--docs-path` and `--genie-space-id`).
- After this work, the old STDF objects in `parijat_demos.manufacturing` were dropped.

### 4. Tab state and the Genie error (2026-10-05, `c89f16a`)
- Tabs lost their chat because the views unmounted on every switch. They now **stay mounted and are hidden**.
- Genie failed on "last 14 days" with `METRIC_VIEW_JOIN_NOT_SUPPORTED`. A Genie instruction and an example SQL now anchor relative dates on `MAX(date)` with a scalar subquery.
- The Genie tab now shows Genie's actual error text, not a generic message.

### 5. Requirements re-check: Lakeflow, Lakebase, refresh (2026-10-05/06, `b9443e3`, `4ed2173`)
I asked Claude Code to check the build against the brief again. It confirmed my suspicion: **there was no Lakebase anywhere**, and the refresh job had no schedule and no way to start it from the app. Ingestion was also still a Python write, not Lakeflow. This pass closed those gaps.

**Lakeflow pipeline** (skill: `databricks-pipelines`)
- `bundle/pipelines/stdf_ingest.sql`: Auto Loader (`read_files`) into five `raw_stdf_*` streaming tables, each with expectations plus `_source_file` and `_ingested_at` columns.
- `generate_data` now writes only parquet files. The pipeline owns the raw tables.
- The raw tables created earlier by CTAS blocked the pipeline from taking ownership and had to be dropped.
- The pipeline runs with `full_refresh: true`, because the generator rewrites the whole dataset on every run.

**Lakebase** (skill: `databricks-lakebase`)
- The bundle declares a Lakebase Autoscaling project `parijat-mfg-ops` and two SNAPSHOT synced tables.
- The first deploy failed with 404s: the synced tables were created before the project existed. Pointing `branch:` at `${resources.postgres_projects.mfg_ops_lakebase.name}/branches/production` fixed the creation order.
- `refresh_lakebase.py` re-snapshots both tables and grants the app's service principal read access. It started on `psycopg[binary]`, which **crashed with SIGABRT** on serverless job compute twice. The first job run only passed because its automatic retry succeeded. I switched to the pure-Python **`pg8000`**, which has passed on every run since.
- **App Lakebase client** (`app/server/lakebase.js`): uses a service-principal client-credentials token and `/api/2.0/postgres/credentials`, with a `pg.Pool` that fetches a fresh password per connection and retries once. It calls the Lakebase and Jobs APIs as the app's service principal. Genie and the Supervisor still use the user's OBO token.
- After deploying, the app said **"Lakebase is not attached"**. The `postgres` resource only injects `PG*` variables, so `LAKEBASE_ENDPOINT` has to be declared in `app.yaml` with `valueFrom: 'lakebase'` (`4ed2173`). Lakebase calls also now return a 503 that names the missing variables.

**App.** Two new tabs:
- **Wafer Operations:** a queue read from Lakebase, an SVG wafer map, and HOLD/REPROBE/RELEASE/SCRAP dispositions written to `wafer_ops.wafer_dispositions`. The app's service principal creates and owns that table.
- **Data Pipeline:** shows job runs and has a Refresh button.

### 6. Build evidence (2026-10-06/07, `ec5ff05`)
- `evidence/collect_evidence.py` exports a full job run, runs the verification notebook on serverless and saves it with its outputs, and captures the bundle and app state, all as plain text.
- PyPI was blocked locally and a sandbox bypass was declined, so this validation runs on Databricks serverless, not on my laptop.

### 7. Rules → ML models (2026-10-07)
I asked: "Can we build an anomaly-detection model instead of rule-based?" Claude Code pointed out that an anomaly model can only say *whether* a map is unusual, not *which* pattern it is, and that `pattern_class` feeds the dashboard, Genie, Lakebase, the app and the KA. It offered three scopes. I chose **both**: an anomaly detector and a classifier.
- **Split the SQL.** `transformations.sql` now stops at `silver_wafer_features`, with the old rule output kept as `rule_pattern_class`. The new `wafer_ml.py` task trains, registers and scores both models. The new `wafer_patterns.sql` task builds `gold_wafer_patterns` from the predictions, with the same column names, so nothing downstream needed rewiring.
- **Honest comparison.** The classifier is evaluated with 5-fold out-of-fold predictions on the 521 labelled wafers, and the rules are scored on the same wafers. Both land in `gold_wafer_model_metrics`.
- **First run failed** on `Decimal / float`, because Spark's `AVG(0.0 …)` returns DECIMAL. Fixed with explicit `DOUBLE` casts.
- **First model lost on Scratch.** It reached 96.2% CV accuracy, but caught only 2 of 8 Scratch wafers against the rules' 7. The rules have a dedicated elongation threshold; the model only had aggregate cluster features. Adding largest-connected-blob shape features (size, elongation, length, radius) took it to **97.1%** (rules 93.1%), Scratch 7/8 and Loc 14/21 (rules 9/21). The Isolation Forest reached ROC AUC 0.988.
- **Claude Code broke the app deploy.** Its local `npm install` for type-checking wrote a `package-lock.json` pinned to the internal npm proxy. The workspace sync uploaded it, and the Apps build failed with 403 errors, leaving the app UNAVAILABLE for about two minutes. Deleting the lockfile and redeploying fixed it. Lesson: never sync a locally generated lockfile to Apps.
- Lakebase SNAPSHOT synced tables picked up the new columns on the next refresh with no changes needed.

---

## Where I changed my mind

| Planned (2026-10-05 plan) | What happened instead | Why |
|---|---|---|
| New schema `semi_yield` | `mfg_ops` | I named it when approving the plan |
| Anomaly-detection model on yield and parameter drift, plus `ai_query` incident summaries | First **wafer-map pattern classification** with a **rule-based SQL classifier** (84% → 93% after tuning). Then (step 7) an **Isolation Forest anomaly detector** plus a **random-forest pattern classifier**, both registered in UC, with the rules kept as a baseline | I chose pattern classification, which the plan called "more impressive, but a lot more work". Rules came first because they're explainable and needed no model ops. They were replaced once the labelled set showed ML could do better: 97.1% vs 93.1%, cross-validated. The anomaly detector is on wafer maps, not on per-tester drift as first planned. `ai_query` summaries were not built |
| Lakeflow for bronze, silver and gold, with a quarantine table | Lakeflow owns **raw ingest only**, with `DROP ROW` expectations. Silver and gold stay in the SQL task | Lakeflow was added late (step 5) to close the ingest gap, so it was scoped to the raw layer. The existing SQL silver and gold tables, metric views and Genie were left as they were. No quarantine table was built |
| UC row filters by site, masked lot IDs and costs, governed tags | **Not built.** Governance today means one schema, table comments, lineage and grants | It was never built, and the requirements re-check didn't flag it. This is the biggest open gap against the "govern it" stage |
| "Excursion Inbox" on Lakebase, with decisions synced back to Delta | **Wafer Operations desk** with write-back. No sync back to Delta yet | I picked "wafer ops desk + write-back". Lakehouse Sync of `wafer_dispositions` into UC was offered as a follow-up, so Genie could answer questions about dispositions |
| Scheduled refresh | **App button only**, no schedule | My call: no idle cost, and the business user sees the refresh happen |
| Recreate the KA and Supervisor from the bundle | **Bind the existing Genie, KA and Supervisor IDs** | Recreating them would break the app's endpoint references and the OAuth consent that had just been fixed |
| Rich tab-state fix (tab in the URL hash, chats kept in `sessionStorage` across reloads) | The commit only **keeps tabs mounted** | The richer version was prototyped and tested in Chrome in the planning session but isn't in the repo. Keep-mounted covers the requirement (state survives switching tabs), but chats don't survive a page reload |
| Terraform deployment engine | **Direct** engine | Terraform can't bind `genie_spaces` |
| `psycopg[binary]` | `pg8000` | Reproducible SIGABRT on serverless |

---

## How the Supervisor Agent is wired

```
App (Agent tab) --OBO--> mas-40863b57-endpoint (Multi-Agent Supervisor)
                            ├── genie-space 01f14033…  -> numbers (metric views in mfg_ops)
                            └── KA ka-b49c04e7-endpoint  -> explanations (corpus in /Volumes/parijat_demos/mfg_ops/raw_data/docs)
```

- `bundle/redeploy_agents.py` (the `sync_agent_bricks` job task) keeps this wiring in sync with the bundle:
  1. it updates the KA's knowledge source to the `mfg_ops` docs path, creating the new source before deleting stale ones;
  2. it waits for the KA to become active;
  3. it updates the Supervisor's agents list (Genie space plus KA) from `--genie-space-id`.
- KA source names must be unique per KA, so they're now derived from the docs path. The first `sync_agent_bricks` run failed with a duplicate-name error.
- The app never calls the KA directly. Replacing the KA would mean changing the Supervisor's agents list, not the app.

---

## Lessons I'd reuse

- **Check against the brief early.** The missing Lakebase stage was only caught when I asked for a requirements re-check, after the docs and deck already existed. Both then had to be updated.
- **App resource ≠ app permission.** Binding a resource, declaring the OAuth scope and getting the user's consent are three separate steps. A stale consent grant gives a 403, with no new consent prompt.
- **Bundles with Agent Bricks or Genie need a bootstrap plan.** On a fresh workspace, Genie, the metric views and the KA have to be created in a set order.
- **Test the real runtime.** The psycopg crash and the missing `LAKEBASE_ENDPOINT` only showed up on serverless and in the deployed app, not in local checks.
