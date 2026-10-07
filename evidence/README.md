# Build evidence

This folder is proof that the build actually ran. Everything here is plain text taken from the live workspace (`e2-demo-field-eng`, catalog `parijat_demos.mfg_ops`) on 2026-10-07. None of it was typed by hand. `collect_evidence.py` regenerates all of it.

| File | What it shows |
|---|---|
| [runs/job_run_968864672363608.md](runs/job_run_968864672363608.md) | An end-to-end run of the refresh job, all 6 tasks SUCCESS in 15m 03s. It includes each task's logged output (data generation quality checks, Lakebase sync refresh, KA/MAS sync) and the Lakeflow pipeline event log. |
| [notebook/build_evidence.ipynb](notebook/build_evidence.ipynb) | The verification notebook ([build_evidence.py](build_evidence.py)) after running on serverless, with cell outputs included |
| [notebook/build_evidence.md](notebook/build_evidence.md) | The same notebook as markdown: code followed by its output |
| [deploy/bundle_summary.txt](deploy/bundle_summary.txt) | Output of `databricks bundle validate` and `bundle summary`: every bundle-owned resource |
| [deploy/app_status.md](deploy/app_status.md) | App state (RUNNING), its resources (Genie, serving endpoint, Lakebase, job), its scopes and its deployments |
| [deploy/app_logs.txt](deploy/app_logs.txt) | App build and runtime logs (client IPs redacted). They show 200s on `/api/genie`, `/api/agent/chat`, `/api/ops/wafers*`, the disposition write-back and `/api/pipeline/status`. The earlier `/api/ops` 503s happened before `LAKEBASE_ENDPOINT` was declared, which commit `4ed2173` fixed. |

## Requirement → evidence

| Requirement | Where to look | Result recorded |
|---|---|---|
| Lakeflow ingest | job run `ingest_raw` event log; notebook §1 | 5 streaming tables, 1,054,926 rows, 0 dropped, every expectation passed |
| Unity Catalog governance | notebook §2–3 | 25 managed tables, 5 streaming tables, 7 metric views, 2 Lakebase synced tables and the `raw_data` volume, all in one schema |
| Transform / incident | notebook §3–4 | AUS MX-7 FPY 95.4% → 88.8% during 2025-08-18..27, then back to 95.1% |
| ML: wafer-map classifier | notebook §5 | 93.1% agreement with 521 engineer-reviewed wafers; Edge-Ring concentrated on PC-AUS-447 |
| Semantic layer + dashboard | notebook §6–7 | Metric-view queries return results; all 14 published dashboard datasets execute |
| Lakebase operational serving | notebook §8 | Both synced tables ONLINE; wafer queue in ~20 ms, die map in ~17 ms; app write-back rows present |
| Genie | notebook §9 | Questions in plain English produce generated SQL, the result rows and Genie's answer |
| GenAI (KA + Supervisor) | notebook §10 | Real answers from `ka-b49c04e7-endpoint` and `mas-40863b57-endpoint`; the supervisor routes to Genie and then the KA |
| Databricks App | notebook §11; deploy/ | App RUNNING, resources attached, and the request logs |

## Regenerate

```bash
# 1. run the refresh job (app Data Pipeline tab, or)
databricks jobs run-now 848871296768056 --profile e2-demo-fe
# 2. collect: exports that run, re-runs the notebook on serverless, captures bundle + app state
python evidence/collect_evidence.py --profile e2-demo-fe --job-run-id <run_id>
```
