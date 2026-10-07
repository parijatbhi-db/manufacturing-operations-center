# Databricks notebook source
# MAGIC %md
# MAGIC # Build evidence: Manufacturing Operations Center (STDF on `parijat_demos.mfg_ops`)
# MAGIC
# MAGIC This notebook checks every asset the bundle and app build, using the live workspace:
# MAGIC Lakeflow ingest, Unity Catalog, the silver/gold layers and wafer-map classifier, the metric views,
# MAGIC the dashboard datasets, Lakebase serving, Genie, the Knowledge Assistant and Supervisor Agent,
# MAGIC and the Databricks App. It runs as a one-time serverless job; `evidence/collect_evidence.py`
# MAGIC exports the run, with its outputs, to `evidence/notebook/`.

# COMMAND ----------

# MAGIC %pip install -q "databricks-sdk>=0.81.0" pg8000 tabulate
# MAGIC %restart_python

# COMMAND ----------

import itertools
import json
import ssl
import time
from datetime import datetime, timezone

import pandas as pd
import pg8000.native as pgn
from databricks.sdk import WorkspaceClient

CATALOG, SCHEMA = 'parijat_demos', 'mfg_ops'
PIPELINE_ID = 'bb686d6e-f137-4d1b-ae5d-924ac9b6629a'
JOB_ID = 848871296768056
GENIE_SPACE_ID = '01f1403396011f339af2cb207b69e9b6'
DASHBOARD_ID = '01f13ffb2116152b9c57017ac6989369'
KA_ENDPOINT = 'ka-b49c04e7-endpoint'
MAS_ENDPOINT = 'mas-40863b57-endpoint'
LAKEBASE_ENDPOINT = 'projects/parijat-mfg-ops/branches/production/endpoints/primary'
LAKEBASE_DB = 'databricks_postgres'
APP_NAME = 'mfg-ops-center'

w = WorkspaceClient()
spark.sql(f'USE CATALOG {CATALOG}')
spark.sql(f'USE SCHEMA {SCHEMA}')
pd.set_option('display.width', 200)


def show(sql, n=20):
    """Run a query and print the result as a markdown table (readable in the committed output)."""
    pdf = spark.sql(sql).limit(n).toPandas()
    print(pdf.to_markdown(index=False, floatfmt='.4f'))
    return pdf


print('Run by:', w.current_user.me().user_name)
print('Run at:', datetime.now(timezone.utc).isoformat(timespec='seconds'))
print('Workspace:', w.config.host)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Lakeflow ingest: latest pipeline update and expectations

# COMMAND ----------

update = w.pipelines.list_updates(pipeline_id=PIPELINE_ID, max_results=1).updates[0]
print('Pipeline:', w.pipelines.get(pipeline_id=PIPELINE_ID).name)
print('Latest update:', update.update_id, update.state.value, 'full_refresh =', update.full_refresh,
      datetime.fromtimestamp(update.creation_time / 1000, timezone.utc).isoformat(timespec='seconds'))

# Row and expectation metrics are reported on the flow's RUNNING progress events
events = spark.sql(f"""
  SELECT origin.flow_name AS flow,
         CAST(details:flow_progress.metrics.num_output_rows AS BIGINT) AS rows_written,
         CAST(details:flow_progress.data_quality.dropped_records AS BIGINT) AS rows_dropped,
         CAST(details:flow_progress.data_quality.expectations AS STRING) AS expectations
  FROM event_log('{PIPELINE_ID}')
  WHERE event_type = 'flow_progress' AND origin.update_id = '{update.update_id}'
    AND details:flow_progress.metrics.num_output_rows IS NOT NULL
""").toPandas()
rows = {}
for r in events.itertuples():
    agg = rows.setdefault(r.flow.split('.')[-1], {'rows_written': 0, 'rows_dropped': 0, 'exp': {}})
    agg['rows_written'] += r.rows_written or 0
    agg['rows_dropped'] += r.rows_dropped or 0
    for e in json.loads(r.expectations) if r.expectations else []:
        p, f = agg['exp'].get(e['name'], (0, 0))
        agg['exp'][e['name']] = (p + e['passed_records'], f + e['failed_records'])
rows = [{'streaming_table': k, 'rows_written': v['rows_written'], 'rows_dropped': v['rows_dropped'],
         'expectations (passed / failed)': '; '.join(f'{n} {p:,}/{f:,}' for n, (p, f) in v['exp'].items())}
        for k, v in sorted(rows.items())]
print(pd.DataFrame(rows).to_markdown(index=False))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Unity Catalog: everything lives in one governed schema

# COMMAND ----------

show(f"""
  SELECT table_type, COUNT(*) AS objects, ARRAY_JOIN(SORT_ARRAY(COLLECT_LIST(table_name)), ', ') AS names
  FROM {CATALOG}.information_schema.tables
  WHERE table_schema = '{SCHEMA}' AND left(table_name, 2) <> '__'  -- skip pipeline-internal tables
  GROUP BY table_type ORDER BY table_type""")
print()
show(f"""
  SELECT table_name, comment FROM {CATALOG}.information_schema.tables
  WHERE table_schema = '{SCHEMA}' AND table_name LIKE 'mv_%' ORDER BY table_name""")
print()
show(f'SHOW VOLUMES IN {CATALOG}.{SCHEMA}')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Row counts across raw, silver and gold

# COMMAND ----------

tables = [r.table_name for r in spark.sql(f"""
  SELECT table_name FROM {CATALOG}.information_schema.tables
  WHERE table_schema = '{SCHEMA}' AND table_type IN ('MANAGED', 'STREAMING_TABLE', 'MATERIALIZED_VIEW')
    AND (table_name LIKE 'raw_%' OR table_name LIKE 'silver_%' OR table_name LIKE 'gold_%')
  ORDER BY table_name""").collect()]
counts = [{'table': t, 'rows': spark.table(t).count()} for t in tables]
print(pd.DataFrame(counts).to_markdown(index=False))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. The incident in the data: FPY drop at AUS / MX-7 (2025-08-18 to 2025-08-27)

# COMMAND ----------

show("""
  SELECT site, product,
         ROUND(AVG(fpy) FILTER (WHERE date < '2025-08-18'), 4) AS fpy_before,
         ROUND(AVG(fpy) FILTER (WHERE date BETWEEN '2025-08-18' AND '2025-08-27'), 4) AS fpy_incident,
         ROUND(AVG(fpy) FILTER (WHERE date > '2025-08-27'), 4) AS fpy_after
  FROM gold_test_kpis_daily GROUP BY site, product ORDER BY site, product""")
print()
show("""
  SELECT site, MAX(cumulative_lost_dies) AS lost_dies, ROUND(MAX(cumulative_cost_usd), 0) AS cost_usd
  FROM gold_impact_cumulative GROUP BY site ORDER BY site""")
print()
show('SELECT change_time, site, tester_id, change_type, ecr_id, details FROM gold_change_log ORDER BY change_time', n=10)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Wafer-map pattern classification (ML-style feature rules vs. engineer review)

# COMMAND ----------

show("""
  SELECT pattern_class, COUNT(*) AS wafers, ROUND(AVG(wafer_yield), 4) AS avg_yield,
         ROUND(AVG(edge_fail_rate), 4) AS avg_edge_fail_rate
  FROM gold_wafer_patterns GROUP BY pattern_class ORDER BY wafers DESC""")
print()
agree = spark.sql("""
  SELECT SUM(wafer_count) FILTER (WHERE is_match) / SUM(wafer_count) AS agreement, SUM(wafer_count) AS reviewed
  FROM gold_wafer_pattern_eval""").first()
print(f'Classifier agreement with engineer review: {agree.agreement:.1%} of {agree.reviewed} reviewed wafers\n')
show("""
  SELECT reviewed_pattern,
         SUM(wafer_count) AS reviewed,
         SUM(wafer_count) FILTER (WHERE is_match) AS matched,
         ROUND(SUM(wafer_count) FILTER (WHERE is_match) / SUM(wafer_count), 3) AS recall
  FROM gold_wafer_pattern_eval GROUP BY reviewed_pattern ORDER BY reviewed DESC""")
print()
print('Root-cause signal: Edge-Ring wafers by tester / probe card')
show("""
  SELECT tester_id, probe_card_id, COUNT(*) AS edge_ring_wafers
  FROM gold_wafer_patterns WHERE pattern_class = 'Edge-Ring'
  GROUP BY tester_id, probe_card_id ORDER BY edge_ring_wafers DESC""", n=8)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Metric views (the Genie and dashboard semantic layer)

# COMMAND ----------

show("""
  SELECT Site, MEASURE(`First Pass Yield`) AS fpy, MEASURE(`Retest Rate`) AS retest_rate,
         MEASURE(`Total Dies Tested`) AS dies_tested
  FROM mv_stdf_test_yield GROUP BY Site ORDER BY Site""")
print()
show("""
  SELECT `Pattern Class`, MEASURE(`Wafer Count`) AS wafers, MEASURE(`Average Wafer Yield`) AS avg_yield
  FROM mv_stdf_wafer_patterns GROUP BY `Pattern Class` ORDER BY wafers DESC""")
print()
show("""
  SELECT Site, MEASURE(`Average UPH`) AS avg_uph FROM mv_stdf_throughput GROUP BY Site ORDER BY Site""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Dashboard: every dataset of the published Lakeview dashboard executes

# COMMAND ----------

dash = w.lakeview.get(dashboard_id=DASHBOARD_ID)
published = w.lakeview.get_published(dashboard_id=DASHBOARD_ID)
print('Dashboard:', dash.display_name, '| lifecycle:', dash.lifecycle_state.value,
      '| published warehouse:', published.warehouse_id)
spec = json.loads(dash.serialized_dashboard)
print('Pages:', ', '.join(p.get('displayName', p['name']) for p in spec['pages']), '\n')
results = []
for ds in spec['datasets']:
    sql = ''.join(ds['queryLines'])
    for p in ds.get('parameters', []):
        # Substitute the dataset parameter's default (e.g. the default wafer on the Wafer Map page)
        default = p['defaultSelection']['values']['values'][0]['value']
        sql = sql.replace(f":{p['keyword']}", f"'{default}'")
    t0 = time.time()
    n = spark.sql(sql).count()
    results.append({'dataset': ds['displayName'], 'rows': n, 'seconds': round(time.time() - t0, 2)})
print(pd.DataFrame(results).to_markdown(index=False))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Lakebase operational serving: synced tables and app write-back

# COMMAND ----------

for name in ['lb_wafer_patterns', 'lb_wafer_map_dies']:
    st = w.postgres.get_synced_table(name=f'synced_tables/{CATALOG}.{SCHEMA}.{name}')
    print(f'{name}: state={st.status.detailed_state.value if st.status.detailed_state else None}',
          f'pipeline={st.status.pipeline_id}')

endpoint = w.postgres.get_endpoint(name=LAKEBASE_ENDPOINT)
conn = pgn.Connection(user=w.current_user.me().user_name,
                      password=w.postgres.generate_database_credential(endpoint=LAKEBASE_ENDPOINT).token,
                      host=endpoint.status.hosts.host, database=LAKEBASE_DB,
                      ssl_context=ssl.create_default_context())


def pg(sql, **params):
    t0 = time.perf_counter()
    rows = conn.run(sql, **params)
    ms = (time.perf_counter() - t0) * 1000
    cols = [c['name'] for c in conn.columns]
    return pd.DataFrame(rows, columns=cols), ms


print('\nPostgres:', pg('SELECT version()')[0].iloc[0, 0][:60])
counts, _ = pg(f"""
  SELECT 'mfg_ops.lb_wafer_patterns' AS pg_table, COUNT(*) AS rows FROM {SCHEMA}.lb_wafer_patterns
  UNION ALL SELECT 'mfg_ops.lb_wafer_map_dies', COUNT(*) FROM {SCHEMA}.lb_wafer_map_dies""")
print(counts.to_markdown(index=False))

# Same queries the app's Wafer Operations tab runs (warm the compute first; it scales to zero)
pg(f'SELECT 1 FROM {SCHEMA}.lb_wafer_patterns LIMIT 1')
queue, q_ms = pg(f"""
  SELECT wafer_id, site, tester_id, probe_card_id, pattern_class, ROUND(wafer_yield::numeric, 3) AS wafer_yield
  FROM {SCHEMA}.lb_wafer_patterns WHERE pattern_class NOT IN ('None', 'Random')
  ORDER BY sort_date DESC, wafer_yield ASC LIMIT 300""")
dies_df, d_ms = pg(f'SELECT die_x, die_y, bin_label FROM {SCHEMA}.lb_wafer_map_dies WHERE wafer_id = :w',
                   w='MX7-F12-W27-W01')
print(f'\nWafer queue: {len(queue)} rows in {q_ms:.1f} ms; die map for MX7-F12-W27-W01: {len(dies_df)} dies in {d_ms:.1f} ms')
print(queue.head(8).to_markdown(index=False))

try:
    disp, _ = pg("""SELECT wafer_id, disposition, pattern_class, tester_id, entered_by, entered_at
                    FROM wafer_ops.wafer_dispositions ORDER BY entered_at DESC LIMIT 10""")
    print('\nApp write-back table wafer_ops.wafer_dispositions (created and owned by the app service principal):')
    print(disp.to_markdown(index=False))
except Exception as e:
    print('\nwafer_ops.wafer_dispositions not readable by this user:', str(e)[:200])
conn.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Genie: natural-language questions answered over the metric views

# COMMAND ----------

space = w.genie.get_space(space_id=GENIE_SPACE_ID)
print('Genie space:', space.title, '\n')
for question in ['What was the first pass yield by site over the last 14 days?',
                 'Which tester and probe card have the most Edge-Ring wafers?']:
    msg = w.genie.start_conversation_and_wait(space_id=GENIE_SPACE_ID, content=question)
    print('Q:', question)
    print('Status:', msg.status.value)
    for att in msg.attachments or []:
        if att.text:
            print('Genie:', att.text.content)
        if att.query:
            print('Generated SQL:\n' + att.query.query)
            res = w.genie.get_message_attachment_query_result(
                space_id=GENIE_SPACE_ID, conversation_id=msg.conversation_id,
                message_id=msg.id, attachment_id=att.attachment_id).statement_response
            cols = [c.name for c in res.manifest.schema.columns]
            print(pd.DataFrame(res.result.data_array or [], columns=cols).head(10).to_markdown(index=False))
    print('-' * 80)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10. GenAI: Knowledge Assistant and Supervisor Agent responses

# COMMAND ----------

def ask(endpoint, question):
    out = w.api_client.do('POST', f'/serving-endpoints/{endpoint}/invocations',
                          body={'input': [{'role': 'user', 'content': question}]})
    text = ''.join(c.get('text', '') for item in out.get('output', []) if item.get('type') == 'message'
                   for c in item.get('content', []) if c.get('type') == 'output_text')
    return text


for endpoint, question in [
    (KA_ENDPOINT, 'What does an Edge-Ring wafer-map pattern usually indicate, and what should a yield engineer check first?'),
    (MAS_ENDPOINT, 'Which tester had the most Edge-Ring wafers, and what is the likely root cause?'),
]:
    state = w.serving_endpoints.get(name=endpoint).state.ready.value
    t0 = time.time()
    answer = ask(endpoint, question)
    print(f'Endpoint: {endpoint} (state {state}), answered in {time.time() - t0:.1f}s')
    print('Q:', question)
    print('A:', answer.strip())
    print('-' * 80)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 11. Databricks App and refresh job

# COMMAND ----------

app = w.apps.get(name=APP_NAME)
print('App:', app.name, '|', app.url)
print('App status:', app.app_status.state.value, '| compute:', app.compute_status.state.value)
print('Active deployment:', app.active_deployment.deployment_id, app.active_deployment.status.state.value,
      app.active_deployment.create_time)
print('Resources:')
for r in app.resources:
    kind = next(k for k in ('genie_space', 'serving_endpoint', 'job', 'postgres', 'sql_warehouse', 'database')
                if getattr(r, k, None))
    print(f'  - {r.name}: {kind}')
print('User API scopes:', app.user_api_scopes)

job = w.jobs.get(job_id=JOB_ID)
print('\nRefresh job:', job.settings.name)
for run in itertools.islice(w.jobs.list_runs(job_id=JOB_ID), 3):
    print(f'  run {run.run_id}: {run.state.result_state.value if run.state.result_state else run.state.life_cycle_state.value}',
          datetime.fromtimestamp(run.start_time / 1000, timezone.utc).isoformat(timespec='seconds'))
