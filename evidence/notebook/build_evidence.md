# build_evidence: executed notebook with outputs

Exported from serverless run [655794724582614](https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/930846601586432/run/655794724582614) (2026-10-07 00:22:53 UTC, result **SUCCESS**). Re-create with `python evidence/collect_evidence.py`.

# Build evidence: Manufacturing Operations Center (STDF on `parijat_demos.mfg_ops`)

This notebook checks every asset the bundle and app build, using the live workspace:
Lakeflow ingest, Unity Catalog, the silver/gold layers and wafer-map classifier, the metric views,
the dashboard datasets, Lakebase serving, Genie, the Knowledge Assistant and Supervisor Agent,
and the Databricks App. It runs as a one-time serverless job; `evidence/collect_evidence.py`
exports the run, with its outputs, to `evidence/notebook/`.

```
%pip install -q "databricks-sdk>=0.81.0" pg8000 tabulate
%restart_python
```

**Output**

```
(pip install output omitted)
```

```python
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
```

**Output**

```
Run by: parijat.bhide@databricks.com
Run at: 2026-10-07T00:23:42+00:00
Workspace: https://e2-demo-field-eng.cloud.databricks.com
```

## 1. Lakeflow ingest: latest pipeline update and expectations

```python
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
```

**Output**

Pipeline: [mfg_ops] STDF raw ingestion (Lakeflow)
Latest update: 693510a6-3710-4738-a604-bebf808262c3 COMPLETED full_refresh = True 2026-10-06T23:57:51+00:00
| streaming_table                  |   rows_written |   rows_dropped | expectations (passed / failed)                                                                      |
|:---------------------------------|---------------:|---------------:|:----------------------------------------------------------------------------------------------------|
| raw_stdf_raw_equip_change_log    |             29 |              0 | valid_change 29/0                                                                                   |
| raw_stdf_raw_lot_wafer_master    |           1200 |              0 | positive_die_count 1,200/0; valid_wafer 1,200/0                                                     |
| raw_stdf_raw_prr_parts           |         565856 |              0 | valid_part_id 565,856/0; known_site 565,856/0; valid_die_coords 565,856/0; known_hard_bin 565,856/0 |
| raw_stdf_raw_ptr_params          |         487320 |              0 | valid_part_id 487,320/0; known_param 487,320/0; finite_value 487,320/0                              |
| raw_stdf_raw_wafer_review_labels |            521 |              0 | valid_label 521/0                                                                                   |

## 2. Unity Catalog: everything lives in one governed schema

```python
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
```

**Output**

| table_type      |   objects | names                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
|:----------------|----------:|:---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| FOREIGN         |         2 | lb_wafer_map_dies, lb_wafer_patterns                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| MANAGED         |        25 | event_log_43da7120_297e_4e28_a4de_cc1f19246dda, event_log_68094f6b_27c9_446f_8ecf_ea5eca7fb6a9, event_log_bb686d6e_f137_4d1b_ae5d_924ac9b6629a, gold_bin_mix_weekly, gold_change_log, gold_counter_avg_fpy_14d, gold_counter_avg_retest_14d, gold_counter_avg_uph_7d, gold_counter_total_dies_30d, gold_filters_bridge, gold_impact_cumulative, gold_param_drift_timeseries, gold_test_kpis_daily, gold_throughput_daily, gold_wafer_map_dies, gold_wafer_pattern_eval, gold_wafer_pattern_weekly, gold_wafer_patterns, silver_equip_change_log, silver_lot_wafer_master, silver_stdf_prr_parts, silver_stdf_ptr_params, silver_time_spine, silver_wafer_review_labels, silver_wafer_sort_sessions |
| METRIC_VIEW     |         7 | mv_stdf_business_impact, mv_stdf_equipment_changes, mv_stdf_failure_bins, mv_stdf_param_drift, mv_stdf_test_yield, mv_stdf_throughput, mv_stdf_wafer_patterns                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| STREAMING_TABLE |         5 | raw_stdf_raw_equip_change_log, raw_stdf_raw_lot_wafer_master, raw_stdf_raw_prr_parts, raw_stdf_raw_ptr_params, raw_stdf_raw_wafer_review_labels                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

| table_name                | comment                                                                       |
|:--------------------------|:------------------------------------------------------------------------------|
| mv_stdf_business_impact   | Cumulative MX-7 lost dies and cost vs FPY baseline by site.                   |
| mv_stdf_equipment_changes | Equipment, firmware, probe card and recipe change log.                        |
| mv_stdf_failure_bins      | Weekly failure hard-bin counts by site and product.                           |
| mv_stdf_param_drift       | Daily parametric drift (z-score vs pre-event baseline) and limit-breach rate. |
| mv_stdf_test_yield        | Daily test yield KPIs by site, product and foundry.                           |
| mv_stdf_throughput        | Daily wafer-sort throughput (UPH) by site and product.                        |
| mv_stdf_wafer_patterns    | Wafer-map pattern classification per wafer sort session.                      |

| database   | volume_name   |
|:-----------|:--------------|
| mfg_ops    | raw_data      |

## 3. Row counts across raw, silver and gold

```python
tables = [r.table_name for r in spark.sql(f"""
  SELECT table_name FROM {CATALOG}.information_schema.tables
  WHERE table_schema = '{SCHEMA}' AND table_type IN ('MANAGED', 'STREAMING_TABLE', 'MATERIALIZED_VIEW')
    AND (table_name LIKE 'raw_%' OR table_name LIKE 'silver_%' OR table_name LIKE 'gold_%')
  ORDER BY table_name""").collect()]
counts = [{'table': t, 'rows': spark.table(t).count()} for t in tables]
print(pd.DataFrame(counts).to_markdown(index=False))
```

**Output**

| table                            |   rows |
|:---------------------------------|-------:|
| gold_bin_mix_weekly              |    550 |
| gold_change_log                  |     29 |
| gold_counter_avg_fpy_14d         |      1 |
| gold_counter_avg_retest_14d      |      1 |
| gold_counter_avg_uph_7d          |      1 |
| gold_counter_total_dies_30d      |      1 |
| gold_filters_bridge              |      1 |
| gold_impact_cumulative           |    217 |
| gold_param_drift_timeseries      |   1731 |
| gold_test_kpis_daily             |    781 |
| gold_throughput_daily            |    577 |
| gold_wafer_map_dies              | 565856 |
| gold_wafer_pattern_eval          |     25 |
| gold_wafer_pattern_weekly        |    380 |
| gold_wafer_patterns              |   1200 |
| raw_stdf_raw_equip_change_log    |     29 |
| raw_stdf_raw_lot_wafer_master    |   1200 |
| raw_stdf_raw_prr_parts           | 565856 |
| raw_stdf_raw_ptr_params          | 487320 |
| raw_stdf_raw_wafer_review_labels |    521 |
| silver_equip_change_log          |     29 |
| silver_lot_wafer_master          |   1200 |
| silver_stdf_prr_parts            | 565856 |
| silver_stdf_ptr_params           | 487320 |
| silver_time_spine                |    113 |
| silver_wafer_review_labels       |    521 |
| silver_wafer_sort_sessions       |   1200 |

## 4. The incident in the data: FPY drop at AUS / MX-7 (2025-08-18 to 2025-08-27)

```python
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
```

**Output**

| site   | product   |   fpy_before |   fpy_incident |   fpy_after |
|:-------|:----------|-------------:|---------------:|------------:|
| AUS    | MX-5      |       0.9483 |         0.9464 |      0.9446 |
| AUS    | MX-7      |       0.9543 |         0.8879 |      0.9512 |
| AUS    | RF-22     |       0.9386 |         0.9531 |      0.9426 |
| HSC    | MX-5      |       0.9527 |         0.9528 |      0.9487 |
| HSC    | MX-7      |       0.9636 |         0.9700 |      0.9669 |
| HSC    | RF-22     |       0.9387 |         0.9562 |      0.9441 |
| PNG    | MX-5      |       0.9534 |       nan      |      0.9520 |
| PNG    | MX-7      |       0.9592 |         0.9641 |      0.9594 |
| PNG    | RF-22     |       0.9393 |         0.9522 |      0.9427 |

| site   |   lost_dies |   cost_usd |
|:-------|------------:|-----------:|
| AUS    |        2266 | 36204.0000 |
| HSC    |         746 | 11871.0000 |
| PNG    |         103 |  1622.0000 |

| change_time         | site   | tester_id   | change_type      | ecr_id           | details                                                          |
|:--------------------|:-------|:------------|:-----------------|:-----------------|:-----------------------------------------------------------------|
| 2025-06-15 08:00:00 | HSC    | TST-HSC-04  | probe_card       | ECR-20250615-196 | probe_card update Rev A; notes include codes PBC-CR-532          |
| 2025-06-22 09:00:00 | AUS    | TST-AUS-08  | limits           | ECR-20250622-361 | limits update ID-200; notes include codes HND-OS-210             |
| 2025-06-29 15:00:00 | AUS    | TST-AUS-02  | probe_card       | ECR-20250629-667 | probe_card update Rev A; notes include codes PBC-CR-532          |
| 2025-07-06 18:00:00 | AUS    | TST-AUS-07  | handler_firmware | ECR-20250706-220 | handler_firmware update HF-3.0.2; notes include codes PBC-CR-532 |
| 2025-07-13 18:00:00 | PNG    | TST-PNG-03  | limits           | ECR-20250713-957 | limits update ID-956; notes include codes TPR-991                |
| 2025-07-20 07:00:00 | HSC    | TST-HSC-02  | probe_card       | ECR-20250720-867 | probe_card update Rev B; notes include codes HND-OS-210          |
| 2025-07-27 12:00:00 | PNG    | TST-PNG-04  | handler_firmware | ECR-20250727-923 | handler_firmware update HF-3.1.7; notes include codes PBC-CR-532 |
| 2025-08-03 14:00:00 | HSC    | TST-HSC-02  | probe_card       | ECR-20250803-822 | probe_card update Rev A; notes include codes TPR-991             |
| 2025-08-10 13:00:00 | AUS    | TST-AUS-07  | limits           | ECR-20250810-254 | limits update ID-914; notes include codes PBC-CR-532             |
| 2025-08-17 13:00:00 | AUS    | TST-AUS-08  | recipe           | ECR-20250817-904 | recipe update ID-572; notes include codes HND-OS-210             |

## 5. Wafer-map pattern classification (ML-style feature rules vs. engineer review)

```python
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
```

**Output**

| pattern_class   |   wafers |   avg_yield |   avg_edge_fail_rate |
|:----------------|---------:|------------:|---------------------:|
| None            |      853 |      0.9630 |               0.0370 |
| Center          |       71 |      0.9309 |               0.0360 |
| Edge-Ring       |       61 |      0.8847 |               0.2641 |
| Edge-Loc        |       60 |      0.9279 |               0.1087 |
| Random          |       54 |      0.8973 |               0.1042 |
| Donut           |       41 |      0.9052 |               0.0411 |
| Scratch         |       35 |      0.9381 |               0.0372 |
| Loc             |       25 |      0.9385 |               0.0388 |

Classifier agreement with engineer review: 93.1% of 521 reviewed wafers

| reviewed_pattern   |   reviewed |   matched |   recall |
|:-------------------|-----------:|----------:|---------:|
| None               |        346 |       338 |   0.9770 |
| Edge-Ring          |         40 |        37 |   0.9250 |
| Edge-Loc           |         34 |        33 |   0.9710 |
| Center             |         30 |        26 |   0.8670 |
| Random             |         25 |        21 |   0.8400 |
| Loc                |         21 |         9 |   0.4290 |
| Donut              |         17 |        14 |   0.8240 |
| Scratch            |          8 |         7 |   0.8750 |

Root-cause signal: Edge-Ring wafers by tester / probe card
| tester_id   | probe_card_id   |   edge_ring_wafers |
|:------------|:----------------|-------------------:|
| TST-AUS-05  | PC-AUS-447      |                 15 |
| TST-AUS-03  | PC-AUS-447      |                 11 |
| TST-AUS-04  | PC-AUS-447      |                 10 |
| TST-AUS-08  | PC-AUS-389      |                  4 |
| TST-HSC-04  | PC-HSC-210      |                  3 |
| TST-HSC-05  | PC-HSC-315      |                  3 |
| TST-AUS-07  | PC-AUS-402      |                  3 |
| TST-AUS-02  | PC-AUS-389      |                  2 |

## 6. Metric views (the Genie and dashboard semantic layer)

```python
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
```

**Output**

| Site   |    fpy |   retest_rate |   dies_tested |
|:-------|-------:|--------------:|--------------:|
| AUS    | 0.9448 |        0.0319 |        335215 |
| HSC    | 0.9588 |        0.0226 |        191099 |
| PNG    | 0.9481 |        0.0283 |         39542 |

| Pattern Class   |   wafers |   avg_yield |
|:----------------|---------:|------------:|
| None            |      853 |      0.9630 |
| Center          |       71 |      0.9309 |
| Edge-Ring       |       61 |      0.8847 |
| Edge-Loc        |       60 |      0.9279 |
| Random          |       54 |      0.8973 |
| Donut           |       41 |      0.9052 |
| Scratch         |       35 |      0.9381 |
| Loc             |       25 |      0.9385 |

| Site   |   avg_uph |
|:-------|----------:|
| AUS    |  590.7434 |
| HSC    |  621.0719 |
| PNG    |  603.8690 |

## 7. Dashboard: every dataset of the published Lakeview dashboard executes

```python
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
```

**Output**

Dashboard: Semiconductor Test Quality and Throughput | lifecycle: ACTIVE | published warehouse: 50cb0e5f102813f0
Pages: Global Filters, Semiconductor Test Quality and Throughput, Wafer Map Patterns 

| dataset                                |   rows |   seconds |
|:---------------------------------------|-------:|----------:|
| Counter: Total Dies Tested (Last 30d)  |      1 |      0.81 |
| Counter: Avg FPY (Last 14d)            |      1 |      0.49 |
| Counter: Avg Retest Rate (Last 14d)    |      1 |      0.53 |
| Daily Test KPIs                        |    781 |      0.61 |
| Weekly Failure Bin Mix                 |    550 |      1.12 |
| Parameter Drift Timeseries             |   1731 |      0.66 |
| Equipment and Recipe Change Log        |     29 |      0.57 |
| Cumulative Impact (Lost Dies and Cost) |    217 |      0.54 |
| Wafer Patterns                         |   1200 |      0.5  |
| Patterned Wafers                       |    293 |      0.58 |
| Weekly Wafer Pattern Mix               |    247 |      0.83 |
| Wafer List                             |   1200 |      0.56 |
| Wafer Map Dies                         |    529 |      0.69 |
| Classifier vs Engineer Review          |     25 |      0.58 |

## 8. Lakebase operational serving: synced tables and app write-back

```python
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
```

**Output**

lb_wafer_patterns: state=SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE pipeline=68094f6b-27c9-446f-8ecf-ea5eca7fb6a9
lb_wafer_map_dies: state=SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE pipeline=43da7120-297e-4e28-a4de-cc1f19246dda

Postgres: PostgreSQL 17.11 (fcae950) on x86_64-pc-linux-gnu, compiled 
| pg_table                  |   rows |
|:--------------------------|-------:|
| mfg_ops.lb_wafer_patterns |   1200 |
| mfg_ops.lb_wafer_map_dies | 565856 |

Wafer queue: 293 rows in 20.0 ms; die map for MX7-F12-W27-W01: 529 dies in 17.0 ms
| wafer_id         | site   | tester_id   | probe_card_id   | pattern_class   |   wafer_yield |
|:-----------------|:-------|:------------|:----------------|:----------------|--------------:|
| MX5-F12-H007-W22 | HSC    | TST-HSC-05  | PC-HSC-210      | Donut           |         0.898 |
| MX5-F10-A009-W17 | AUS    | TST-AUS-04  | PC-AUS-447      | Donut           |         0.909 |
| MX7-F12-H013-W12 | HSC    | TST-HSC-01  | PC-HSC-315      | Donut           |         0.911 |
| MX5-F12-A009-W06 | AUS    | TST-AUS-03  | PC-AUS-447      | Edge-Ring       |         0.884 |
| MX7-F12-A012-W25 | AUS    | TST-AUS-05  | PC-AUS-447      | Edge-Loc        |         0.932 |
| MX5-F12-A009-W04 | AUS    | TST-AUS-03  | PC-AUS-447      | Center          |         0.937 |
| MX7-F10-A012-W03 | AUS    | TST-AUS-05  | PC-AUS-447      | Center          |         0.941 |
| MX7-F12-A014-W01 | AUS    | TST-AUS-07  | PC-AUS-402      | Donut           |         0.958 |

App write-back table wafer_ops.wafer_dispositions (created and owned by the app service principal):
| wafer_id        | disposition   | pattern_class   | tester_id   | entered_by                   | entered_at                       |
|:----------------|:--------------|:----------------|:------------|:-----------------------------|:---------------------------------|
| MX7-F12-W29-W01 | SCRAP         | Edge-Ring       | TST-AUS-04  | parijat.bhide@databricks.com | 2026-10-06 18:38:15.218832+00:00 |

## 9. Genie: natural-language questions answered over the metric views

```python
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
```

**Output**

Genie space: STDF Semiconductor Test Quality Analytics 

Q: What was the first pass yield by site over the last 14 days?
Status: COMPLETED
Generated SQL:
SELECT
  `Site`,
  MEASURE(`First Pass Yield`) AS `First Pass Yield`
FROM `parijat_demos`.`mfg_ops`.`mv_stdf_test_yield`
WHERE `Date` > DATE_SUB((SELECT MAX(`Date`) FROM `parijat_demos`.`mfg_ops`.`mv_stdf_test_yield`), 14)
  AND `Site` IS NOT NULL
GROUP BY ALL
ORDER BY `Site` ASC
| Site   |   First Pass Yield |
|:-------|-------------------:|
| AUS    |           0.946639 |
| HSC    |           0.957541 |
| PNG    |           0.95071  |
Genie: Over the last 14 days in the data, first pass yield was **94.66%** at **AUS**, **95.75%** at **HSC**, and **95.07%** at **PNG**. Among the three sites, **HSC** had the highest yield and **AUS** the lowest.
--------------------------------------------------------------------------------
Q: Which tester and probe card have the most Edge-Ring wafers?
Status: COMPLETED
Generated SQL:
WITH ranked_edge_ring AS (
  SELECT
    `Tester ID`,
    `Probe Card ID`,
    MEASURE(`Wafer Count`) AS `Edge-Ring Wafers`,
    RANK() OVER (ORDER BY MEASURE(`Wafer Count`) DESC) AS `wafer_rank`
  FROM `parijat_demos`.`mfg_ops`.`mv_stdf_wafer_patterns`
  WHERE `Pattern Class` = 'Edge-Ring'
    AND `Tester ID` IS NOT NULL
    AND `Probe Card ID` IS NOT NULL
  GROUP BY ALL
)
SELECT
  `Tester ID`,
  `Probe Card ID`,
  `Edge-Ring Wafers`
FROM ranked_edge_ring
WHERE `wafer_rank` <= 1
ORDER BY `Edge-Ring Wafers` DESC, `Tester ID` ASC, `Probe Card ID` ASC
| Tester ID   | Probe Card ID   |   Edge-Ring Wafers |
|:------------|:----------------|-------------------:|
| TST-AUS-05  | PC-AUS-447      |                 15 |
Genie: The tester and probe card with the most **Edge-Ring** wafers are **Tester ID TST-AUS-05** and **Probe Card ID PC-AUS-447**, with **15 Edge-Ring wafers**. In the returned result set, **15 wafers** is the highest Edge-Ring count shown.
--------------------------------------------------------------------------------

## 10. GenAI: Knowledge Assistant and Supervisor Agent responses

```python
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
```

**Output**

```
Endpoint: ka-b49c04e7-endpoint (state READY), answered in 7.6s
Q: What does an Edge-Ring wafer-map pattern usually indicate, and what should a yield engineer check first?
A: An Edge-Ring wafer-map pattern indicates failures all around the wafer edge, typically caused by probe card planarity/contact issues or bevel etch problems.

When encountering an Edge-Ring pattern, a yield engineer should first check:

1. **Probe card condition and planarity** – Edge-Ring patterns are associated with probe card planarity and contact issues, so the probe card should be inspected for wear, contamination, or misalignment.

2. **Contact resistance measurements** – In the Austin MX-7 yield anomaly, affected wafers showed elevated PT_0210 contact resistance at the edge, indicating that parametric drift in contact resistance should be monitored.

3. **Recent equipment changes** – The Austin incident was traced to probe card PC-AUS-447 Rev C installation combined with handler firmware HF-3.2.1 deployment on specific testers, so any recent probe card replacements, firmware updates, or tester configuration changes should be reviewed.

The Edge-Ring pattern is particularly important to catch early, as it can significantly impact yield (the Austin MX-7 incident caused FPY to drop ~6.8 points from baseline) and reduce throughput due to slower probe indexing.
--------------------------------------------------------------------------------
Endpoint: mas-40863b57-endpoint (state READY), answered in 34.3s
Q: Which tester had the most Edge-Ring wafers, and what is the likely root cause?
A: I'll help you find which tester had the most Edge-Ring wafers and investigate the likely root cause. Let me start by querying the test data.<name>Genie_Data_Explorer</name>||Tester ID|Edge-Ring Wafer Count|
|-|-|-|
|0|TST-AUS-05|15|
|1|TST-AUS-03|11|
|2|TST-AUS-04|10|
|3|TST-AUS-08|5|
|4|TST-AUS-07|3|
|5|TST-HSC-04|3|
|6|TST-HSC-05|3|
|7|TST-AUS-01|2|
|8|TST-AUS-02|2|
|9|TST-HSC-06|2|
|10|TST-PNG-02|2|
|11|TST-AUS-06|1|
|12|TST-HSC-01|1|
|13|TST-HSC-03|1|<name>Manufacturing-Operations-Supervisor</name>Now let me search for information about the root cause of Edge-Ring wafer failures.<name>STDF-Test-Quality-KA</name>Edge-Ring failures are caused by probe card planarity/contact issues or bevel etch problems, and they result in failures all around the wafer edge.

In the context of the documented Austin MX-7 yield anomaly, the Edge-Ring pattern was specifically caused by probe card PC-AUS-447 Rev C installation combined with handler firmware HF-3.2.1 deployment on testers TST-AUS-03, TST-AUS-04, and TST-AUS-05. The failing dies were concentrated at the wafer edge in hard bin HB_021 (open/short contact) with elevated PT_0210 contact resistance at the edge.[^sBfQ-1]

[^sBfQ-1]: ## Key Event: Austin MX-7 Yield Anomaly - **Period**: August 18-27, 2025 - **Site**: AUS (Austin) - **Product**: MX-7 (lots MX7-F12-W27..W29 from foundry F12 were in sort during the window) - **Root Cause**: Probe card PC-AUS-447 Rev C installation + handler firmware HF-3.2.1 deployment on testers TST-AUS-03, TST-AUS-04, TST-AUS-05 - **Wafer-map signature**: Edge-Ring pattern on affected wafers; failing dies concentrated at the wafer edge in hard bin HB_021 (open/short contact) with elevated PT_0210 contact resistance at the edge - **Impact**: FPY dropped ~6.8 points from baseline (~95.3% to ~88.5%), retest rate rose to 7-9%, slower probe indexing reduced UPH - **Recovery**: Firmware rollback to HF-3.1.9 and probe card recondition on Aug 24 at 22:15, FPY normalized by Sep 1 - **Sites**: AUS (Austin), HSC (Hsinchu), PNG (Penang) - **Products**: MX-5, MX-7, RF-22 - **Foundries**: F10, F12  ## Hard Bins - HB_001 / HB_002: pass bins - HB_014: IDDQ / leakage failure - HB_021: open/short (contact) failure - HB_007: functional failure - HB_032: parametric (Vth) failure  ## Wafer-Map Pattern Classes Every wafer is probed in one sort session, so each wafer has a complete die map.  [stdf_data_dictionary.md](https://e2-demo-field-eng.cloud.databricks.com/ajax-api/2.0/fs/files/Volumes/parijat_demos/mfg_ops/raw_data/docs/stdf_data_dictionary.md#:~:text=%23%23%20Key%20Event%3A%20Austin%20MX-7%20Yield%20Anomaly%0A-%20%2A%2APeriod%2A%2A%3A%20August%2018-27%2C%202025%0A-%20%2A%2ASite%2A%2A%3A%20AUS%20%28Austin%29%0A-%20%2A%2AProduct%2A%2A%3A%20MX-7%20%28lots%20MX7-F12-W27..W29%20from%20foundry%20F12%20were%20in%20sort%20during%20the%20window%29%0A-%20%2A%2ARoot%20Cause%2A%2A%3A%20Probe%20card%20PC-AUS-447%20Rev%20C%20installation%20%2B%20handler%20firmware%20HF-3.2.1%20deployment%20on%20testers%20TST-AUS-03%2C%20TST-AUS-04%2C%20TST-AUS-05%0A-%20%2A%2AWafer-map%20signature%2A%2A%3A%20Edge-Ring%20pattern%20on%20affected%20wafers%3B%20failing%20dies%20concentrated%20at%20the%20wafer%20edge%20in%20hard%20bin%20HB_021%20%28open/short%20contact%29%20with%20elevated%20PT_0210%20contact%20resistance%20at%20the%20edge%0A-%20%2A%2AImpact%2A%2A%3A%20FPY%20dropped%20~6.8%20points%20from%20baseline%20%28~95.3%25%20to%20~88.5%25%29%2C%20retest%20rate%20rose%20to%207-9%25%2C%20slower%20probe%20indexing%20reduced%20UPH%0A-%20%2A%2ARecovery%2A%2A%3A%20Firmware%20rollback%20to%20HF-3.1.9%20and%20probe%20card%20recondition%20on%20Aug%2024%20at%2022%3A15%2C%20FPY%20normalized%20by%20Sep%201%0A-%20%2A%2ASites%2A%2A%3A%20AUS%20%28Austin%29%2C%20HSC%20%28Hsinchu%29%2C%20PNG%20%28Penang%29%0A-%20%2A%2AProducts%2A%2A%3A%20MX-5%2C%20MX-7%2C%20RF-22%0A-%20%2A%2AFoundries%2A%2A%3A%20F10%2C%20F12%0A%0A%23%23%20Hard%20Bins%0A-%20HB_001%20/%20HB_002%3A%20pass%20bins%0A-%20HB_014%3A%20IDDQ%20/%20leakage%20failure%0A-%20HB_021%3A%20open/short%20%28contact%29%20failure%0A-%20HB_007%3A%20functional%20failure%0A-%20HB_032%3A%20parametric%20%28Vth%29%20failure%0A%0A%23%23%20Wafer-Map%20Pattern%20Classes%0AEvery%20wafer%20is%20probed%20in%20one%20sort%20session%2C%20so%20each%20wafer%20has%20a%20complete%20die%20map.%20)<name>Manufacturing-Operations-Supervisor</name>**Tester TST-AUS-05** had the most Edge-Ring wafers with **15 wafers**.

**Likely Root Cause:**

Based on the incident documentation, Edge-Ring failures are typically caused by:

1. **Probe card planarity/contact issues** - The probe card is not making uniform contact across the wafer, particularly at the edges
2. **Bevel etch problems** - Issues with the wafer edge preparation

In the specific case documented for the Austin facility, the Edge-Ring pattern was caused by the combination of:
- **Probe card PC-AUS-447 Rev C** installation
- **Handler firmware HF-3.2.1** deployment

This affected testers TST-AUS-03, TST-AUS-04, and TST-AUS-05 (which aligns with your data showing these three testers having the highest Edge-Ring wafer counts: 15, 11, and 10 respectively). The failures manifested as:
- Dies concentrated at the wafer edge
- Hard bin HB_021 (open/short contact)
- Elevated PT_0210 contact resistance at the edge

The root cause is **probe card contact issues** exacerbated by incompatible handler firmware, leading to poor electrical contact at the wafer periphery.
--------------------------------------------------------------------------------
```

## 11. Databricks App and refresh job

```python
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
```

**Output**

```
App: mfg-ops-center | https://mfg-ops-center-1444828305810485.aws.databricksapps.com
App status: RUNNING | compute: ACTIVE
Active deployment: 01f1c1b48d351980989aa432cf186c56 SUCCEEDED 2026-10-06T18:34:23Z
Resources:
  - genie-space: genie_space
  - serving-endpoint: serving_endpoint
  - lakebase: postgres
  - refresh-job: job
User API scopes: ['dashboards.genie', 'serving.serving-endpoints']

Refresh job: [mfg_ops] STDF pipeline - ingest, transform, serve
  run 968864672363608: SUCCESS 2026-10-06T23:55:55+00:00
  run 23880951221004: SUCCESS 2026-10-06T01:35:40+00:00
  run 490954671298243: SUCCESS 2026-10-06T01:03:22+00:00
```

