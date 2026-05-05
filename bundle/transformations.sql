-- ==================================================================================
-- KARI Semiconductor STDF Ingestion Demo - Silver and Gold Transformations
-- Catalog/Schema: mfg_central.stdf
-- Purpose: Build cleaned Silver layer and business-ready Gold layer per demo story.
-- ==================================================================================

-- ======================================
-- SILVER LAYER (clean/derive/no aggregation)
-- ======================================

-- Helper: daily time spine from 2025-06-15..2025-10-05
CREATE OR REPLACE TABLE parijat_demos.manufacturing.silver_time_spine AS
SELECT
  d AS date,
  DAYOFWEEK(d) AS dow,
  CASE WHEN DAYOFWEEK(d) IN (1,7) THEN TRUE ELSE FALSE END AS is_weekend,
  DATE_TRUNC('WEEK', d) AS week_start,
  DATE_TRUNC('MONTH', d) AS month
FROM (
  SELECT EXPLODE(SEQUENCE(
           DATE('2025-06-15'),
           DATE('2025-10-05'),
           INTERVAL 1 DAY
         )) AS d
) s;
ALTER TABLE parijat_demos.manufacturing.silver_time_spine SET TBLPROPERTIES ('comment' = 'Daily calendar helper used to scaffold trends for FPY, bin mix, parameter drift, and impact.');

-- Silver: PRR Parts (atomic die-level results)
CREATE OR REPLACE TABLE parijat_demos.manufacturing.silver_stdf_prr_parts AS
SELECT
  part_id,
  UPPER(TRIM(site)) AS site,
  tester_id,
  wafer_id,
  lot_id,
  UPPER(TRIM(foundry)) AS foundry,
  UPPER(TRIM(product)) AS product,
  CAST(timestamp AS TIMESTAMP) AS timestamp,
  CAST(timestamp AS DATE) AS date,
  DATE_TRUNC('WEEK', CAST(timestamp AS DATE)) AS week_start,
  soft_bin,
  hard_bin,
  CAST(pass_flag AS BOOLEAN) AS pass_flag,
  CAST(retest_flag AS BOOLEAN) AS retest_flag,
  program_version,
  probe_card_id,
  handler_id,
  -- Optional die_index parsing (best-effort; may be null if pattern differs)
  REGEXP_EXTRACT(part_id, '.*-X([0-9]+)Y([0-9]+)$', 1) AS die_x,
  REGEXP_EXTRACT(part_id, '.*-X([0-9]+)Y([0-9]+)$', 2) AS die_y
FROM parijat_demos.manufacturing.raw_stdf_raw_prr_parts;
ALTER TABLE parijat_demos.manufacturing.silver_stdf_prr_parts SET TBLPROPERTIES ('comment' = 'Cleaned PRR part-level table. Adds date/week, standardizes enums, keeps pass/retest flags, bins, equipment identifiers. Granularity: one row per part_id event.');

-- Silver: PTR Params (atomic param measurements; derive zscore/cpk)
-- Baseline window: trailing 21 days pre-event by {site, product, param_code}
-- We compute zscore using baseline mean/std from dates before the anomaly (pre-event reference per site/product/param).
CREATE OR REPLACE TABLE parijat_demos.manufacturing.silver_stdf_ptr_params AS
WITH base AS (
  SELECT
    CAST(p.timestamp AS DATE) AS date,
    CAST(p.timestamp AS TIMESTAMP) AS timestamp,
    p.part_id,
    UPPER(TRIM(p.param_code)) AS param_code,
    p.value,
    p.lsl,
    p.usl,
    COALESCE(pr.site, UPPER(TRIM(p.site))) AS site,
    COALESCE(pr.product, UPPER(TRIM(p.product))) AS product,
    COALESCE(pr.wafer_id, p.wafer_id) AS wafer_id
  FROM parijat_demos.manufacturing.raw_stdf_raw_ptr_params p
  LEFT JOIN parijat_demos.manufacturing.silver_stdf_prr_parts pr
    ON p.part_id = pr.part_id
),
pre_event_baseline AS (
  -- Pre-event baseline across trailing 21 days prior to 2025-08-18 per site/product/param
  SELECT
    site,
    product,
    param_code,
    AVG(value) AS baseline_mean,
    STDDEV_POP(value) AS baseline_std
  FROM base
  WHERE date BETWEEN DATE('2025-07-28') AND DATE('2025-08-17')
  GROUP BY site, product, param_code
)
SELECT
  b.part_id,
  b.date,
  b.site,
  b.product,
  b.wafer_id,
  b.param_code,
  b.value,
  b.lsl,
  b.usl,
  CASE
    WHEN pb.baseline_std IS NULL OR pb.baseline_std = 0 THEN NULL
    ELSE (b.value - pb.baseline_mean) / pb.baseline_std
  END AS zscore,
  -- Cpk (two-sided when both limits exist; else one-sided)
  CASE
    WHEN b.lsl IS NOT NULL AND b.usl IS NOT NULL AND b.lsl < b.usl THEN
      LEAST((b.usl - b.value) / (3 * NULLIF(pb.baseline_std, 0)), (b.value - b.lsl) / (3 * NULLIF(pb.baseline_std, 0)))
    WHEN b.usl IS NOT NULL THEN (b.usl - b.value) / (3 * NULLIF(pb.baseline_std, 0))
    WHEN b.lsl IS NOT NULL THEN (b.value - b.lsl) / (3 * NULLIF(pb.baseline_std, 0))
    ELSE NULL
  END AS cpk,
  CASE
    WHEN b.lsl IS NOT NULL AND b.value < b.lsl THEN TRUE
    WHEN b.usl IS NOT NULL AND b.value > b.usl THEN TRUE
    ELSE FALSE
  END AS limit_breach
FROM base b
LEFT JOIN pre_event_baseline pb
  ON b.site = pb.site AND b.product = pb.product AND b.param_code = pb.param_code;
ALTER TABLE parijat_demos.manufacturing.silver_stdf_ptr_params SET TBLPROPERTIES ('comment' = 'Cleaned PTR param-level table joined to PRR for site/product/wafer and date. Adds zscore using pre-event baseline (2025-07-28..2025-08-17) by site/product/param_code, computes Cpk and limit_breach flag.');

-- Silver: Equipment Change Log
CREATE OR REPLACE TABLE parijat_demos.manufacturing.silver_equip_change_log AS
SELECT
  CAST(change_time AS TIMESTAMP) AS change_time,
  CAST(change_time AS DATE) AS date,
  DATE_TRUNC('WEEK', CAST(change_time AS DATE)) AS week_start,
  UPPER(TRIM(site)) AS site,
  tester_id,
  LOWER(TRIM(change_type)) AS change_type,
  scope,
  ecr_id,
  details
FROM parijat_demos.manufacturing.raw_stdf_raw_equip_change_log;
ALTER TABLE parijat_demos.manufacturing.silver_equip_change_log SET TBLPROPERTIES ('comment' = 'Normalized equipment/recipe change log with date and week_start for alignment to metrics.');

-- Silver: Lot/Wafer Master
CREATE OR REPLACE TABLE parijat_demos.manufacturing.silver_lot_wafer_master AS
SELECT
  lot_id,
  wafer_id,
  UPPER(TRIM(product)) AS product,
  UPPER(TRIM(foundry)) AS foundry,
  dies_per_wafer_expected,
  CAST(start_date AS DATE) AS start_date,
  UPPER(TRIM(site_planned)) AS site_planned
FROM parijat_demos.manufacturing.raw_stdf_raw_lot_wafer_master;
ALTER TABLE parijat_demos.manufacturing.silver_lot_wafer_master SET TBLPROPERTIES ('comment' = 'Lot and wafer reference with expected dies per wafer, start date, and planned site.');

-- ======================================
-- GOLD LAYER (aggregations supporting dashboards & questions)
-- ======================================

-- Gold: Test KPIs Daily (FPY, dies, retest) per {date, site, product, foundry}
CREATE OR REPLACE TABLE parijat_demos.manufacturing.gold_test_kpis_daily AS
WITH base AS (
  SELECT
    pr.date,
    pr.site,
    pr.product,
    pr.foundry,
    COUNT(*) AS dies_tested,
    COUNT_IF(pr.pass_flag = TRUE AND pr.retest_flag = FALSE) AS dies_pass_fp,
    COUNT_IF(pr.pass_flag = FALSE) AS dies_fail,
    AVG(CASE WHEN pr.retest_flag THEN 1.0 ELSE 0.0 END) AS retest_rate
  FROM parijat_demos.manufacturing.silver_stdf_prr_parts pr
  GROUP BY pr.date, pr.site, pr.product, pr.foundry
),
-- Trailing 21-day baseline FPY per {site, product, foundry, date}
with_baseline AS (
  SELECT
    b.*,
    AVG(CASE WHEN dies_tested > 0 THEN dies_pass_fp / dies_tested ELSE NULL END) OVER (
      PARTITION BY b.site, b.product, b.foundry
      ORDER BY b.date
      ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING
    ) AS fpy_baseline_21d
  FROM base b
)
SELECT
  date,
  site,
  product,
  foundry,
  dies_tested,
  dies_pass_fp,
  dies_fail,
  ROUND(CASE WHEN dies_tested > 0 THEN dies_pass_fp / dies_tested ELSE NULL END, 4) AS fpy,
  ROUND(retest_rate, 4) AS retest_rate,
  ROUND(fpy_baseline_21d, 4) AS fpy_baseline_21d
FROM with_baseline;
ALTER TABLE parijat_demos.manufacturing.gold_test_kpis_daily SET TBLPROPERTIES ('comment' = 'Daily test KPIs per date/site/product/foundry: dies_tested, first-pass passes, fails, FPY, retest_rate, and trailing-21d FPY baseline. Drives FPY lines and counters.');

-- Gold: Failure Bin Mix Weekly (exclude primary pass bins HB_001/HB_002; top-5 per site/product/week)
CREATE OR REPLACE TABLE parijat_demos.manufacturing.gold_bin_mix_weekly AS
WITH weekly AS (
  SELECT
    pr.week_start,
    pr.site,
    pr.product,
    pr.hard_bin,
    COUNT(*) AS die_count
  FROM parijat_demos.manufacturing.silver_stdf_prr_parts pr
  WHERE pr.hard_bin NOT IN ('HB_001','HB_002')
  GROUP BY pr.week_start, pr.site, pr.product, pr.hard_bin
),
ranked AS (
  SELECT
    week_start,
    site,
    product,
    hard_bin,
    die_count,
    ROW_NUMBER() OVER (PARTITION BY week_start, site, product ORDER BY die_count DESC) AS rn,
    CASE WHEN ROW_NUMBER() OVER (PARTITION BY week_start, site, product ORDER BY die_count DESC) <= 5 THEN hard_bin ELSE 'Other' END AS hard_bin_group
  FROM weekly
)
SELECT
  week_start,
  site,
  product,
  hard_bin_group AS hard_bin,
  SUM(die_count) AS die_count
FROM ranked
GROUP BY week_start, site, product, hard_bin_group;
ALTER TABLE parijat_demos.manufacturing.gold_bin_mix_weekly SET TBLPROPERTIES ('comment' = 'Weekly grouped failure bin counts per site/product. Top-5 hard bins kept, others collapsed to Other. Shows HB_021 surge during AUS incident.');

-- Gold: Parameter Drift Timeseries (daily mean zscore and pct limit breaches for selected params)
CREATE OR REPLACE TABLE parijat_demos.manufacturing.gold_param_drift_timeseries AS
SELECT
  p.date,
  p.site,
  p.product,
  p.param_code,
  ROUND(AVG(p.zscore), 4) AS mean_z,
  ROUND(AVG(CASE WHEN p.limit_breach THEN 1.0 ELSE 0.0 END), 4) AS pct_limit_breach
FROM parijat_demos.manufacturing.silver_stdf_ptr_params p
WHERE p.param_code IN ('PT_0210','PT_0217','PT_0103')
GROUP BY p.date, p.site, p.product, p.param_code;
ALTER TABLE parijat_demos.manufacturing.gold_param_drift_timeseries SET TBLPROPERTIES ('comment' = 'Daily parameter drift metrics by site/product/param_code: mean zscore vs pre-event baseline and percent limit breaches. Expect PT_0210 spike at AUS in 2025-08-18..2025-08-27.');

-- Gold: Throughput Daily (UPH) - approximate using active test hours derived from first/last timestamps per tester/date
CREATE OR REPLACE TABLE parijat_demos.manufacturing.gold_throughput_daily AS
WITH per_tester AS (
  SELECT
    pr.date,
    pr.site,
    pr.product,
    pr.tester_id,
    COUNT(*) AS dies_tested,
    (UNIX_TIMESTAMP(MAX(pr.timestamp)) - UNIX_TIMESTAMP(MIN(pr.timestamp))) / 3600.0 AS active_test_hours
  FROM parijat_demos.manufacturing.silver_stdf_prr_parts pr
  GROUP BY pr.date, pr.site, pr.product, pr.tester_id
),
uph AS (
  SELECT
    date,
    site,
    product,
    tester_id,
    dies_tested,
    CASE WHEN active_test_hours IS NULL OR active_test_hours <= 0 THEN NULL ELSE dies_tested / active_test_hours END AS units_per_hour
  FROM per_tester
)
SELECT
  date,
  site,
  product,
  ROUND(AVG(units_per_hour), 2) AS avg_units_per_hour,
  SUM(dies_tested) AS dies_tested
FROM uph
GROUP BY date, site, product;
ALTER TABLE parijat_demos.manufacturing.gold_throughput_daily SET TBLPROPERTIES ('comment' = 'Daily UPH by site/product computed from tester-level active test hours (span between first and last timestamp). Used for throughput counter and anomaly dip at AUS.');

-- Gold: Change Log (pass-through of silver)
CREATE OR REPLACE TABLE parijat_demos.manufacturing.gold_change_log AS
SELECT *
FROM parijat_demos.manufacturing.silver_equip_change_log;
ALTER TABLE parijat_demos.manufacturing.gold_change_log SET TBLPROPERTIES ('comment' = 'Equipment and recipe changes with dates and details. Used to correlate onset (HF-3.2.1, probe card Rev C on 2025-08-18) and recovery (rollback on 2025-08-24).');

-- Gold: Impact Cumulative (lost dies and costs using baseline FPY and retest costs)
CREATE OR REPLACE TABLE parijat_demos.manufacturing.gold_impact_cumulative AS
WITH kpi AS (
  SELECT * FROM parijat_demos.manufacturing.gold_test_kpis_daily
),
-- Expected yield: use baseline FPY prior to anomaly (from trailing 21d baseline already computed)
calc AS (
  SELECT
    date,
    site,
    product,
    foundry,
    dies_tested,
    dies_pass_fp,
    fpy,
    fpy_baseline_21d,
    -- Lost dies relative to baseline yield expectation
    CASE
      WHEN fpy_baseline_21d IS NULL THEN 0
      ELSE GREATEST(0, CAST(ROUND(fpy_baseline_21d * dies_tested) AS BIGINT) - dies_pass_fp)
    END AS lost_dies,
    -- Retest count approximation
    CAST(ROUND(retest_rate * dies_tested) AS BIGINT) AS retest_count,
    -- Cost model: $15 per die lost at wafer sort + $0.35 per retest
    ROUND(
      (CASE WHEN fpy_baseline_21d IS NULL THEN 0 ELSE GREATEST(0, (fpy_baseline_21d * dies_tested) - dies_pass_fp) END) * 15.0
      + (retest_rate * dies_tested) * 0.35
    , 2) AS cost_usd
  FROM kpi
  WHERE product = 'MX-7'
),
by_date_site AS (
  SELECT
    date,
    site,
    SUM(lost_dies) AS lost_dies,
    ROUND(SUM(cost_usd), 2) AS cost_usd
  FROM calc
  GROUP BY date, site
)
SELECT
  date,
  site,
  SUM(SUM(lost_dies)) OVER (PARTITION BY site ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cumulative_lost_dies,
  ROUND(SUM(SUM(cost_usd)) OVER (PARTITION BY site ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 2) AS cumulative_cost_usd
FROM by_date_site
GROUP BY date, site
ORDER BY date, site;
ALTER TABLE parijat_demos.manufacturing.gold_impact_cumulative SET TBLPROPERTIES ('comment' = 'Daily cumulative lost dies and cost by site using FPY baseline expectation and retest costs. Focused on MX-7 to quantify incident impact and recovery slope.');

-- Gold: Filters Bridge (dimensions/min-max dates)
CREATE OR REPLACE TABLE parijat_demos.manufacturing.gold_filters_bridge AS
SELECT
  (SELECT MIN(date) FROM parijat_demos.manufacturing.silver_stdf_prr_parts) AS min_date,
  (SELECT MAX(date) FROM parijat_demos.manufacturing.silver_stdf_prr_parts) AS max_date,
  ARRAY_SORT(COLLECT_SET(site)) AS sites,
  ARRAY_SORT(COLLECT_SET(product)) AS products,
  ARRAY_SORT(COLLECT_SET(foundry)) AS foundries,
  ARRAY_SORT(COLLECT_SET(tester_id)) AS tester_ids,
  ARRAY_SORT(COLLECT_SET(wafer_id)) AS wafer_ids,
  ARRAY_SORT(COLLECT_SET(lot_id)) AS lot_ids
FROM parijat_demos.manufacturing.silver_stdf_prr_parts;
ALTER TABLE parijat_demos.manufacturing.gold_filters_bridge SET TBLPROPERTIES ('comment' = 'Dimension values and date bounds for building dashboard filters quickly (site, product, foundry, tester_id, wafer_id, lot_id).');

-- =============================
-- Counter helper tables (single-row for dashboard counters)
-- =============================

-- Counter: Total Dies Tested (Last 30 days across filters)
CREATE OR REPLACE TABLE parijat_demos.manufacturing.gold_counter_total_dies_30d AS
WITH maxd AS (SELECT MAX(date) AS max_date FROM parijat_demos.manufacturing.silver_stdf_prr_parts),
range AS (
  SELECT DATE_SUB((SELECT max_date FROM maxd), 29) AS start_date, (SELECT max_date FROM maxd) AS end_date
)
SELECT
  SUM(CASE WHEN date BETWEEN (SELECT start_date FROM range) AND (SELECT end_date FROM range) THEN 1 ELSE 0 END) AS total_dies_30d
FROM parijat_demos.manufacturing.silver_stdf_prr_parts;
ALTER TABLE parijat_demos.manufacturing.gold_counter_total_dies_30d SET TBLPROPERTIES ('comment' = 'Single-row KPI: total dies tested over trailing 30 days for context.');

-- Counter: Avg FPY (Last 14 days) across data
CREATE OR REPLACE TABLE parijat_demos.manufacturing.gold_counter_avg_fpy_14d AS
WITH maxd AS (SELECT MAX(date) AS max_date FROM parijat_demos.manufacturing.gold_test_kpis_daily),
range AS (
  SELECT DATE_SUB((SELECT max_date FROM maxd), 13) AS start_date, (SELECT max_date FROM maxd) AS end_date
)
SELECT
  ROUND(AVG(fpy), 4) AS avg_fpy_14d
FROM parijat_demos.manufacturing.gold_test_kpis_daily
WHERE date BETWEEN (SELECT start_date FROM range) AND (SELECT end_date FROM range);
ALTER TABLE parijat_demos.manufacturing.gold_counter_avg_fpy_14d SET TBLPROPERTIES ('comment' = 'Single-row KPI: average FPY over the last 14 days across current filters.');

-- Counter: Retest Rate (Last 14 days)
CREATE OR REPLACE TABLE parijat_demos.manufacturing.gold_counter_avg_retest_14d AS
WITH maxd AS (SELECT MAX(date) AS max_date FROM parijat_demos.manufacturing.gold_test_kpis_daily),
range AS (
  SELECT DATE_SUB((SELECT max_date FROM maxd), 13) AS start_date, (SELECT max_date FROM maxd) AS end_date
)
SELECT
  ROUND(AVG(retest_rate), 4) AS avg_retest_rate_14d
FROM parijat_demos.manufacturing.gold_test_kpis_daily
WHERE date BETWEEN (SELECT start_date FROM range) AND (SELECT end_date FROM range);
ALTER TABLE parijat_demos.manufacturing.gold_counter_avg_retest_14d SET TBLPROPERTIES ('comment' = 'Single-row KPI: average retest rate over the last 14 days across filters.');

-- Counter: UPH at Wafer Sort (Trailing 7d)
CREATE OR REPLACE TABLE parijat_demos.manufacturing.gold_counter_avg_uph_7d AS
WITH maxd AS (SELECT MAX(date) AS max_date FROM parijat_demos.manufacturing.gold_throughput_daily),
range AS (
  SELECT DATE_SUB((SELECT max_date FROM maxd), 6) AS start_date, (SELECT max_date FROM maxd) AS end_date
)
SELECT
  ROUND(AVG(avg_units_per_hour), 2) AS avg_uph_7d
FROM parijat_demos.manufacturing.gold_throughput_daily
WHERE date BETWEEN (SELECT start_date FROM range) AND (SELECT end_date FROM range);
ALTER TABLE parijat_demos.manufacturing.gold_counter_avg_uph_7d SET TBLPROPERTIES ('comment' = 'Single-row KPI: average UPH over the trailing 7 days.');

-- =============================
-- Table comments detail (documentation and story alignment)
-- =============================

ALTER TABLE parijat_demos.manufacturing.silver_stdf_prr_parts SET TBLPROPERTIES ('comment' = 'Silver PRR parts: standardized enums, date/week helpers, flags, bins, equipment ids. Supports yield and bin mix computations, retest analytics, and throughput derivations.');
ALTER TABLE parijat_demos.manufacturing.silver_stdf_ptr_params SET TBLPROPERTIES ('comment' = 'Silver PTR params: joins to PRR for site/product/wafer/date, adds baseline-derived zscore, cpk, and limit breach flags to support parameter drift analytics.');
ALTER TABLE parijat_demos.manufacturing.silver_equip_change_log SET TBLPROPERTIES ('comment' = 'Silver change log: normalized fields, date/week_start for alignment to anomaly and recovery windows.');
ALTER TABLE parijat_demos.manufacturing.silver_lot_wafer_master SET TBLPROPERTIES ('comment' = 'Silver lot/wafer master: product/foundry enums, expected dies_per_wafer, start_date, planned site; supports impact and segmentation.');

ALTER TABLE parijat_demos.manufacturing.gold_test_kpis_daily SET TBLPROPERTIES ('comment' = 'Gold KPIs: daily FPY, dies tested, fails, retest rate, and trailing-21d FPY baseline by site/product/foundry; backs FPY line and counters.');
ALTER TABLE parijat_demos.manufacturing.gold_bin_mix_weekly SET TBLPROPERTIES ('comment' = 'Gold weekly failure bin mix: top-5 bins per site/product/week, others grouped to Other.');
ALTER TABLE parijat_demos.manufacturing.gold_param_drift_timeseries SET TBLPROPERTIES ('comment' = 'Gold param drift: mean zscore and limit breach percentage daily for selected parameters (PT_0210, PT_0217, PT_0103).');
ALTER TABLE parijat_demos.manufacturing.gold_throughput_daily SET TBLPROPERTIES ('comment' = 'Gold throughput daily: site/product UPH derived from tester spans; highlights AUS dip during incident.');
ALTER TABLE parijat_demos.manufacturing.gold_change_log SET TBLPROPERTIES ('comment' = 'Gold change log passthrough for dashboard table.');
ALTER TABLE parijat_demos.manufacturing.gold_impact_cumulative SET TBLPROPERTIES ('comment' = 'Gold impact cumulative: cumulative lost dies and cost by site using FPY baseline and retest costs; highlights business impact and recovery slope.');
ALTER TABLE parijat_demos.manufacturing.gold_filters_bridge SET TBLPROPERTIES ('comment' = 'Gold filters bridge: dimension lists and min/max dates for dashboard filters.');
ALTER TABLE parijat_demos.manufacturing.gold_counter_total_dies_30d SET TBLPROPERTIES ('comment' = 'Gold counter: total dies tested over trailing 30 days.');
ALTER TABLE parijat_demos.manufacturing.gold_counter_avg_fpy_14d SET TBLPROPERTIES ('comment' = 'Gold counter: average FPY across last 14 days.');
ALTER TABLE parijat_demos.manufacturing.gold_counter_avg_retest_14d SET TBLPROPERTIES ('comment' = 'Gold counter: average retest rate across last 14 days.');
ALTER TABLE parijat_demos.manufacturing.gold_counter_avg_uph_7d SET TBLPROPERTIES ('comment' = 'Gold counter: average UPH across trailing 7 days.');
