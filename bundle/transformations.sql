-- ==================================================================================
-- STDF Manufacturing Operations Demo - Silver and Gold Transformations
-- Target: <catalog>.<schema> (SQL task parameters `catalog` / `schema`, set in databricks.yml)
-- Purpose: Build cleaned Silver layer, business-ready Gold layer, wafer-map pattern
--          classification, and the metric views consumed by Genie.
-- ==================================================================================

USE CATALOG IDENTIFIER(:catalog);
USE SCHEMA IDENTIFIER(:schema);

-- ======================================
-- SILVER LAYER (clean/derive/no aggregation)
-- ======================================

-- Helper: daily time spine from 2025-06-15..2025-10-05
CREATE OR REPLACE TABLE silver_time_spine AS
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
ALTER TABLE silver_time_spine SET TBLPROPERTIES ('comment' = 'Daily calendar helper used to scaffold trends for FPY, bin mix, parameter drift, and impact.');

-- Silver: PRR Parts (atomic die-level results)
CREATE OR REPLACE TABLE silver_stdf_prr_parts AS
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
  -- Die coordinates on the wafer map (STDF PRR X_COORD / Y_COORD)
  CAST(x_coord AS INT) AS die_x,
  CAST(y_coord AS INT) AS die_y
FROM raw_stdf_raw_prr_parts;
ALTER TABLE silver_stdf_prr_parts SET TBLPROPERTIES ('comment' = 'Cleaned PRR part-level table. Adds date/week, standardizes enums, keeps pass/retest flags, bins, equipment identifiers. Granularity: one row per part_id event.');

-- Silver: PTR Params (atomic param measurements; derive zscore/cpk)
-- Baseline window: trailing 21 days pre-event by {site, product, param_code}
-- We compute zscore using baseline mean/std from dates before the anomaly (pre-event reference per site/product/param).
CREATE OR REPLACE TABLE silver_stdf_ptr_params AS
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
  FROM raw_stdf_raw_ptr_params p
  LEFT JOIN silver_stdf_prr_parts pr
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
ALTER TABLE silver_stdf_ptr_params SET TBLPROPERTIES ('comment' = 'Cleaned PTR param-level table joined to PRR for site/product/wafer and date. Adds zscore using pre-event baseline (2025-07-28..2025-08-17) by site/product/param_code, computes Cpk and limit_breach flag.');

-- Silver: Equipment Change Log
CREATE OR REPLACE TABLE silver_equip_change_log AS
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
FROM raw_stdf_raw_equip_change_log;
ALTER TABLE silver_equip_change_log SET TBLPROPERTIES ('comment' = 'Normalized equipment/recipe change log with date and week_start for alignment to metrics.');

-- Silver: Lot/Wafer Master
CREATE OR REPLACE TABLE silver_lot_wafer_master AS
SELECT
  lot_id,
  wafer_id,
  UPPER(TRIM(product)) AS product,
  UPPER(TRIM(foundry)) AS foundry,
  dies_per_wafer_expected,
  CAST(start_date AS DATE) AS start_date,
  UPPER(TRIM(site_planned)) AS site_planned
FROM raw_stdf_raw_lot_wafer_master;
ALTER TABLE silver_lot_wafer_master SET TBLPROPERTIES ('comment' = 'Lot and wafer reference with expected dies per wafer, start date, and planned site.');

-- ======================================
-- GOLD LAYER (aggregations supporting dashboards & questions)
-- ======================================

-- Gold: Test KPIs Daily (FPY, dies, retest) per {date, site, product, foundry}
CREATE OR REPLACE TABLE gold_test_kpis_daily AS
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
  FROM silver_stdf_prr_parts pr
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
ALTER TABLE gold_test_kpis_daily SET TBLPROPERTIES ('comment' = 'Daily test KPIs per date/site/product/foundry: dies_tested, first-pass passes, fails, FPY, retest_rate, and trailing-21d FPY baseline. Drives FPY lines and counters.');

-- Gold: Failure Bin Mix Weekly (exclude primary pass bins HB_001/HB_002; top-5 per site/product/week)
CREATE OR REPLACE TABLE gold_bin_mix_weekly AS
WITH weekly AS (
  SELECT
    pr.week_start,
    pr.site,
    pr.product,
    pr.hard_bin,
    COUNT(*) AS die_count
  FROM silver_stdf_prr_parts pr
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
ALTER TABLE gold_bin_mix_weekly SET TBLPROPERTIES ('comment' = 'Weekly grouped failure bin counts per site/product. Top-5 hard bins kept, others collapsed to Other. Shows HB_021 surge during AUS incident.');

-- Gold: Parameter Drift Timeseries (daily mean zscore and pct limit breaches for selected params)
CREATE OR REPLACE TABLE gold_param_drift_timeseries AS
SELECT
  p.date,
  p.site,
  p.product,
  p.param_code,
  ROUND(AVG(p.zscore), 4) AS mean_z,
  ROUND(AVG(CASE WHEN p.limit_breach THEN 1.0 ELSE 0.0 END), 4) AS pct_limit_breach
FROM silver_stdf_ptr_params p
WHERE p.param_code IN ('PT_0210','PT_0217','PT_0103')
GROUP BY p.date, p.site, p.product, p.param_code;
ALTER TABLE gold_param_drift_timeseries SET TBLPROPERTIES ('comment' = 'Daily parameter drift metrics by site/product/param_code: mean zscore vs pre-event baseline and percent limit breaches. Expect PT_0210 spike at AUS in 2025-08-18..2025-08-27.');

-- Gold: Throughput Daily (UPH) - approximate using active test hours derived from first/last timestamps per tester/date
CREATE OR REPLACE TABLE gold_throughput_daily AS
WITH per_tester AS (
  SELECT
    pr.date,
    pr.site,
    pr.product,
    pr.tester_id,
    COUNT(*) AS dies_tested,
    (UNIX_TIMESTAMP(MAX(pr.timestamp)) - UNIX_TIMESTAMP(MIN(pr.timestamp))) / 3600.0 AS active_test_hours
  FROM silver_stdf_prr_parts pr
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
ALTER TABLE gold_throughput_daily SET TBLPROPERTIES ('comment' = 'Daily UPH by site/product computed from tester-level active test hours (span between first and last timestamp). Used for throughput counter and anomaly dip at AUS.');

-- Gold: Change Log (pass-through of silver)
CREATE OR REPLACE TABLE gold_change_log AS
SELECT *
FROM silver_equip_change_log;
ALTER TABLE gold_change_log SET TBLPROPERTIES ('comment' = 'Equipment and recipe changes with dates and details. Used to correlate onset (HF-3.2.1, probe card Rev C on 2025-08-18) and recovery (rollback on 2025-08-24).');

-- Gold: Impact Cumulative (lost dies and costs using baseline FPY and retest costs)
CREATE OR REPLACE TABLE gold_impact_cumulative AS
WITH kpi AS (
  SELECT * FROM gold_test_kpis_daily
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
ALTER TABLE gold_impact_cumulative SET TBLPROPERTIES ('comment' = 'Daily cumulative lost dies and cost by site using FPY baseline expectation and retest costs. Focused on MX-7 to quantify incident impact and recovery slope.');

-- Gold: Filters Bridge (dimensions/min-max dates)
CREATE OR REPLACE TABLE gold_filters_bridge AS
SELECT
  (SELECT MIN(date) FROM silver_stdf_prr_parts) AS min_date,
  (SELECT MAX(date) FROM silver_stdf_prr_parts) AS max_date,
  ARRAY_SORT(COLLECT_SET(site)) AS sites,
  ARRAY_SORT(COLLECT_SET(product)) AS products,
  ARRAY_SORT(COLLECT_SET(foundry)) AS foundries,
  ARRAY_SORT(COLLECT_SET(tester_id)) AS tester_ids,
  ARRAY_SORT(COLLECT_SET(wafer_id)) AS wafer_ids,
  ARRAY_SORT(COLLECT_SET(lot_id)) AS lot_ids
FROM silver_stdf_prr_parts;
ALTER TABLE gold_filters_bridge SET TBLPROPERTIES ('comment' = 'Dimension values and date bounds for building dashboard filters quickly (site, product, foundry, tester_id, wafer_id, lot_id).');

-- =============================
-- Counter helper tables (single-row for dashboard counters)
-- =============================

-- Counter: Total Dies Tested (Last 30 days across filters)
CREATE OR REPLACE TABLE gold_counter_total_dies_30d AS
WITH maxd AS (SELECT MAX(date) AS max_date FROM silver_stdf_prr_parts),
range AS (
  SELECT DATE_SUB((SELECT max_date FROM maxd), 29) AS start_date, (SELECT max_date FROM maxd) AS end_date
)
SELECT
  SUM(CASE WHEN date BETWEEN (SELECT start_date FROM range) AND (SELECT end_date FROM range) THEN 1 ELSE 0 END) AS total_dies_30d
FROM silver_stdf_prr_parts;
ALTER TABLE gold_counter_total_dies_30d SET TBLPROPERTIES ('comment' = 'Single-row KPI: total dies tested over trailing 30 days for context.');

-- Counter: Avg FPY (Last 14 days) across data
CREATE OR REPLACE TABLE gold_counter_avg_fpy_14d AS
WITH maxd AS (SELECT MAX(date) AS max_date FROM gold_test_kpis_daily),
range AS (
  SELECT DATE_SUB((SELECT max_date FROM maxd), 13) AS start_date, (SELECT max_date FROM maxd) AS end_date
)
SELECT
  ROUND(AVG(fpy), 4) AS avg_fpy_14d
FROM gold_test_kpis_daily
WHERE date BETWEEN (SELECT start_date FROM range) AND (SELECT end_date FROM range);
ALTER TABLE gold_counter_avg_fpy_14d SET TBLPROPERTIES ('comment' = 'Single-row KPI: average FPY over the last 14 days across current filters.');

-- Counter: Retest Rate (Last 14 days)
CREATE OR REPLACE TABLE gold_counter_avg_retest_14d AS
WITH maxd AS (SELECT MAX(date) AS max_date FROM gold_test_kpis_daily),
range AS (
  SELECT DATE_SUB((SELECT max_date FROM maxd), 13) AS start_date, (SELECT max_date FROM maxd) AS end_date
)
SELECT
  ROUND(AVG(retest_rate), 4) AS avg_retest_rate_14d
FROM gold_test_kpis_daily
WHERE date BETWEEN (SELECT start_date FROM range) AND (SELECT end_date FROM range);
ALTER TABLE gold_counter_avg_retest_14d SET TBLPROPERTIES ('comment' = 'Single-row KPI: average retest rate over the last 14 days across filters.');

-- Counter: UPH at Wafer Sort (Trailing 7d)
CREATE OR REPLACE TABLE gold_counter_avg_uph_7d AS
WITH maxd AS (SELECT MAX(date) AS max_date FROM gold_throughput_daily),
range AS (
  SELECT DATE_SUB((SELECT max_date FROM maxd), 6) AS start_date, (SELECT max_date FROM maxd) AS end_date
)
SELECT
  ROUND(AVG(avg_units_per_hour), 2) AS avg_uph_7d
FROM gold_throughput_daily
WHERE date BETWEEN (SELECT start_date FROM range) AND (SELECT end_date FROM range);
ALTER TABLE gold_counter_avg_uph_7d SET TBLPROPERTIES ('comment' = 'Single-row KPI: average UPH over the trailing 7 days.');

-- =============================
-- WAFER-MAP PATTERN CLASSIFICATION
-- =============================
-- Each wafer is probed in one sort session, so the PRR die results form a complete wafer map.
-- Spatial features are computed per wafer here. wafer_ml.py trains an anomaly detector and a pattern
-- classifier on them; wafer_patterns.sql then builds gold_wafer_patterns from the model output.

-- Silver: Wafer review labels (yield-engineer visual classification for a sample of wafers)
CREATE OR REPLACE TABLE silver_wafer_review_labels AS
SELECT
  wafer_id,
  reviewed_pattern,
  reviewer,
  CAST(review_time AS TIMESTAMP) AS review_time
FROM raw_stdf_raw_wafer_review_labels;
ALTER TABLE silver_wafer_review_labels SET TBLPROPERTIES ('comment' = 'Yield-engineer visual wafer-map labels for a sample of wafers (all incident wafers included). Used to measure classifier agreement.');

-- Silver: Wafer sort sessions (one row per wafer probe session)
CREATE OR REPLACE TABLE silver_wafer_sort_sessions AS
SELECT
  wafer_id,
  FIRST(lot_id) AS lot_id,
  FIRST(site) AS site,
  FIRST(product) AS product,
  FIRST(foundry) AS foundry,
  FIRST(tester_id) AS tester_id,
  FIRST(probe_card_id) AS probe_card_id,
  FIRST(handler_id) AS handler_id,
  FIRST(program_version) AS program_version,
  MIN(timestamp) AS sort_start,
  MAX(timestamp) AS sort_end,
  CAST(MIN(timestamp) AS DATE) AS sort_date,
  DATE_TRUNC('WEEK', CAST(MIN(timestamp) AS DATE)) AS week_start,
  COUNT(*) AS dies_tested,
  COUNT_IF(pass_flag) AS dies_pass,
  COUNT_IF(retest_flag) AS dies_retested,
  ROUND(COUNT_IF(pass_flag) / COUNT(*), 4) AS wafer_yield,
  (MIN(die_x) + MAX(die_x)) / 2.0 AS center_x,
  (MIN(die_y) + MAX(die_y)) / 2.0 AS center_y,
  (MAX(die_x) - MIN(die_x)) / 2.0 AS radius_dies
FROM silver_stdf_prr_parts
GROUP BY wafer_id;
ALTER TABLE silver_wafer_sort_sessions SET TBLPROPERTIES ('comment' = 'One row per wafer sort session: lot, site, product, tester/probe card/handler, sort start/end, dies tested/passed/retested, wafer yield, and die-grid center/radius used for wafer-map geometry.');

-- Gold: Wafer map dies (die-level map with polar coordinates for wafer-map visuals)
CREATE OR REPLACE TABLE gold_wafer_map_dies AS
SELECT
  p.wafer_id,
  s.lot_id,
  s.site,
  s.product,
  s.tester_id,
  s.probe_card_id,
  s.sort_date,
  p.die_x,
  p.die_y,
  ROUND(SQRT(POW(p.die_x - s.center_x, 2) + POW(p.die_y - s.center_y, 2)) / s.radius_dies, 4) AS r_norm,
  ATAN2(p.die_y - s.center_y, p.die_x - s.center_x) AS theta,
  CASE
    WHEN SQRT(POW(p.die_x - s.center_x, 2) + POW(p.die_y - s.center_y, 2)) / s.radius_dies < 0.35 THEN 'Center'
    WHEN SQRT(POW(p.die_x - s.center_x, 2) + POW(p.die_y - s.center_y, 2)) / s.radius_dies < 0.65 THEN 'Inner'
    WHEN SQRT(POW(p.die_x - s.center_x, 2) + POW(p.die_y - s.center_y, 2)) / s.radius_dies < 0.82 THEN 'Outer'
    ELSE 'Edge'
  END AS radial_zone,
  p.hard_bin,
  p.soft_bin,
  p.pass_flag,
  p.retest_flag,
  CASE WHEN p.pass_flag THEN 'Pass' ELSE p.hard_bin END AS bin_label
FROM silver_stdf_prr_parts p
JOIN silver_wafer_sort_sessions s ON p.wafer_id = s.wafer_id;
ALTER TABLE gold_wafer_map_dies SET TBLPROPERTIES ('comment' = 'Die-level wafer map: die_x/die_y grid coordinates, normalized radius r_norm (0 center .. 1 edge), angle theta, radial_zone (Center/Inner/Outer/Edge), bins and pass/retest flags. Backs wafer-map scatter plots.');

-- Silver: Wafer spatial features (model inputs) + rule-based baseline class
-- The pattern_class served downstream comes from the ML models in wafer_ml.py; the rule class is
-- kept only as the baseline that gold_wafer_model_metrics compares the classifier against.
CREATE OR REPLACE TABLE silver_wafer_features AS
WITH dies AS (
  SELECT
    wafer_id, die_x, die_y, r_norm, theta,
    NOT pass_flag AS fail,
    CASE WHEN r_norm < 0.35 THEN 0 WHEN r_norm < 0.65 THEN 1 WHEN r_norm < 0.82 THEN 2 ELSE 3 END AS ring,
    CASE WHEN r_norm < 0.35 THEN 0 ELSE LEAST(7, CAST(FLOOR((theta + PI()) / (PI() / 4)) AS INT)) END AS sector
  FROM gold_wafer_map_dies
),
wafer AS (
  SELECT
    wafer_id,
    COUNT(*) AS n_dies,
    COUNT_IF(fail) AS n_fail,
    COUNT_IF(fail) / COUNT(*) AS fail_rate,
    COUNT_IF(fail AND ring = 0) / NULLIF(COUNT_IF(ring = 0), 0) AS center_fail_rate,
    COUNT_IF(fail AND ring = 1) / NULLIF(COUNT_IF(ring = 1), 0) AS inner_fail_rate,
    COUNT_IF(fail AND ring = 2) / NULLIF(COUNT_IF(ring = 2), 0) AS outer_fail_rate,
    COUNT_IF(fail AND ring = 3) / NULLIF(COUNT_IF(ring = 3), 0) AS edge_fail_rate,
    -- Angular concentration of edge-zone fails (0 = spread around the rim, 1 = one spot)
    SQRT(POW(AVG(CASE WHEN fail AND r_norm >= 0.75 THEN COS(theta) END), 2)
       + POW(AVG(CASE WHEN fail AND r_norm >= 0.75 THEN SIN(theta) END), 2)) AS edge_fail_angular_conc,
    -- Angular concentration of interior fails
    SQRT(POW(AVG(CASE WHEN fail AND r_norm < 0.75 AND r_norm >= 0.2 THEN COS(theta) END), 2)
       + POW(AVG(CASE WHEN fail AND r_norm < 0.75 AND r_norm >= 0.2 THEN SIN(theta) END), 2)) AS inner_fail_angular_conc
  FROM dies
  GROUP BY wafer_id
),
-- Chi-square dispersion of fail counts across 25 ring/sector cells: ~1 for spatially random fails
cells AS (
  SELECT d.wafer_id, d.ring, d.sector, COUNT(*) AS n, COUNT_IF(d.fail) AS k
  FROM dies d
  GROUP BY d.wafer_id, d.ring, d.sector
),
dispersion AS (
  SELECT
    c.wafer_id,
    SUM(POW(c.k - c.n * w.fail_rate, 2) / NULLIF(c.n * w.fail_rate * (1 - w.fail_rate), 0)) / (COUNT(*) - 1) AS spatial_chi2_ratio
  FROM cells c
  JOIN wafer w ON c.wafer_id = w.wafer_id
  GROUP BY c.wafer_id
),
-- Clustered fails: failing dies with >= 2 failing 8-neighbours (filters isolated random defects)
fails AS (
  SELECT wafer_id, die_x, die_y, r_norm FROM dies WHERE fail
),
clustered AS (
  SELECT a.wafer_id, a.die_x, a.die_y, a.r_norm
  FROM fails a
  JOIN fails b
    ON a.wafer_id = b.wafer_id
   AND b.die_x BETWEEN a.die_x - 1 AND a.die_x + 1
   AND b.die_y BETWEEN a.die_y - 1 AND a.die_y + 1
   AND NOT (a.die_x = b.die_x AND a.die_y = b.die_y)
  GROUP BY a.wafer_id, a.die_x, a.die_y, a.r_norm
  HAVING COUNT(*) >= 2
),
cluster_shape AS (
  SELECT
    wafer_id,
    COUNT(*) AS n_clustered_fail,
    AVG(r_norm) AS clustered_mean_r,
    VAR_POP(die_x) AS vx,
    VAR_POP(die_y) AS vy,
    COVAR_POP(die_x, die_y) AS cxy
  FROM clustered
  GROUP BY wafer_id
),
features AS (
  SELECT
    w.*,
    d.spatial_chi2_ratio,
    COALESCE(c.n_clustered_fail, 0) AS n_clustered_fail,
    -- Clustered fails relative to what spatially random defects at this fail rate would produce
    COALESCE(c.n_clustered_fail, 0) / GREATEST(
      w.n_fail * (1 - POW(1 - w.fail_rate, 8) - 8 * w.fail_rate * POW(1 - w.fail_rate, 7)), 1.0) AS cluster_excess,
    c.clustered_mean_r,
    -- Elongation = ratio of principal-axis variances of clustered fails (large for scratches)
    ((c.vx + c.vy) / 2 + SQRT(POW((c.vx - c.vy) / 2, 2) + POW(c.cxy, 2)))
      / GREATEST((c.vx + c.vy) / 2 - SQRT(POW((c.vx - c.vy) / 2, 2) + POW(c.cxy, 2)), 0.05) AS clustered_elongation
  FROM wafer w
  JOIN dispersion d ON w.wafer_id = d.wafer_id
  LEFT JOIN cluster_shape c ON w.wafer_id = c.wafer_id
),
classified AS (
  SELECT
    f.*,
    CASE
      WHEN fail_rate >= 0.50 THEN 'Near-full'
      WHEN n_clustered_fail < 5 OR (spatial_chi2_ratio < 2.0 AND cluster_excess < 2.0) THEN
        CASE WHEN fail_rate >= 0.08 THEN 'Random' ELSE 'None' END
      WHEN clustered_mean_r >= 0.75 THEN
        CASE WHEN edge_fail_angular_conc >= 0.5 THEN 'Edge-Loc' ELSE 'Edge-Ring' END
      WHEN clustered_elongation >= 15 AND n_clustered_fail >= 6 THEN 'Scratch'
      WHEN center_fail_rate >= GREATEST(inner_fail_rate, outer_fail_rate, edge_fail_rate)
           AND clustered_mean_r < 0.4 THEN 'Center'
      WHEN inner_fail_angular_conc < 0.35 AND clustered_mean_r BETWEEN 0.3 AND 0.75 THEN 'Donut'
      ELSE 'Loc'
    END AS rule_pattern_class
  FROM features f
)
SELECT
  c.wafer_id,
  c.n_dies,
  c.n_fail,
  ROUND(c.fail_rate, 4) AS fail_rate,
  ROUND(c.center_fail_rate, 4) AS center_fail_rate,
  ROUND(c.inner_fail_rate, 4) AS inner_fail_rate,
  ROUND(c.outer_fail_rate, 4) AS outer_fail_rate,
  ROUND(c.edge_fail_rate, 4) AS edge_fail_rate,
  ROUND(c.edge_fail_angular_conc, 3) AS edge_fail_angular_conc,
  ROUND(c.inner_fail_angular_conc, 3) AS inner_fail_angular_conc,
  ROUND(c.spatial_chi2_ratio, 2) AS spatial_chi2_ratio,
  c.n_clustered_fail,
  ROUND(c.cluster_excess, 2) AS cluster_excess,
  ROUND(c.clustered_mean_r, 3) AS clustered_mean_r,
  ROUND(c.clustered_elongation, 2) AS clustered_elongation,
  c.rule_pattern_class,
  l.reviewed_pattern
FROM classified c
LEFT JOIN silver_wafer_review_labels l ON c.wafer_id = l.wafer_id;
ALTER TABLE silver_wafer_features SET TBLPROPERTIES ('comment' = 'One row per wafer: spatial wafer-map features (zone fail rates, angular concentration, chi-square dispersion, clustered-fail shape) used as model inputs, the rule-based baseline class rule_pattern_class, and the engineer label reviewed_pattern where reviewed.');

-- =============================
-- Table comments detail (documentation and story alignment)
-- =============================

ALTER TABLE silver_stdf_prr_parts SET TBLPROPERTIES ('comment' = 'Silver PRR parts: standardized enums, date/week helpers, flags, bins, equipment ids. Supports yield and bin mix computations, retest analytics, and throughput derivations.');
ALTER TABLE silver_stdf_ptr_params SET TBLPROPERTIES ('comment' = 'Silver PTR params: joins to PRR for site/product/wafer/date, adds baseline-derived zscore, cpk, and limit breach flags to support parameter drift analytics.');
ALTER TABLE silver_equip_change_log SET TBLPROPERTIES ('comment' = 'Silver change log: normalized fields, date/week_start for alignment to anomaly and recovery windows.');
ALTER TABLE silver_lot_wafer_master SET TBLPROPERTIES ('comment' = 'Silver lot/wafer master: product/foundry enums, expected dies_per_wafer, start_date, planned site; supports impact and segmentation.');

ALTER TABLE gold_test_kpis_daily SET TBLPROPERTIES ('comment' = 'Gold KPIs: daily FPY, dies tested, fails, retest rate, and trailing-21d FPY baseline by site/product/foundry; backs FPY line and counters.');
ALTER TABLE gold_bin_mix_weekly SET TBLPROPERTIES ('comment' = 'Gold weekly failure bin mix: top-5 bins per site/product/week, others grouped to Other.');
ALTER TABLE gold_param_drift_timeseries SET TBLPROPERTIES ('comment' = 'Gold param drift: mean zscore and limit breach percentage daily for selected parameters (PT_0210, PT_0217, PT_0103).');
ALTER TABLE gold_throughput_daily SET TBLPROPERTIES ('comment' = 'Gold throughput daily: site/product UPH derived from tester spans; highlights AUS dip during incident.');
ALTER TABLE gold_change_log SET TBLPROPERTIES ('comment' = 'Gold change log passthrough for dashboard table.');
ALTER TABLE gold_impact_cumulative SET TBLPROPERTIES ('comment' = 'Gold impact cumulative: cumulative lost dies and cost by site using FPY baseline and retest costs; highlights business impact and recovery slope.');
ALTER TABLE gold_filters_bridge SET TBLPROPERTIES ('comment' = 'Gold filters bridge: dimension lists and min/max dates for dashboard filters.');
ALTER TABLE gold_counter_total_dies_30d SET TBLPROPERTIES ('comment' = 'Gold counter: total dies tested over trailing 30 days.');
ALTER TABLE gold_counter_avg_fpy_14d SET TBLPROPERTIES ('comment' = 'Gold counter: average FPY across last 14 days.');
ALTER TABLE gold_counter_avg_retest_14d SET TBLPROPERTIES ('comment' = 'Gold counter: average retest rate across last 14 days.');
ALTER TABLE gold_counter_avg_uph_7d SET TBLPROPERTIES ('comment' = 'Gold counter: average UPH across trailing 7 days.');

-- =============================
-- METRIC VIEWS (Genie data sources)
-- =============================

CREATE OR REPLACE VIEW mv_stdf_test_yield
COMMENT 'Daily test yield KPIs by site, product and foundry.'
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: gold_test_kpis_daily
dimensions:
  - name: Date
    expr: date
  - name: Site
    expr: site
  - name: Product
    expr: product
  - name: Foundry
    expr: foundry
measures:
  - name: Total Dies Tested
    expr: SUM(dies_tested)
  - name: Dies Passed First Pass
    expr: SUM(dies_pass_fp)
  - name: Dies Failed
    expr: SUM(dies_fail)
  - name: First Pass Yield
    expr: SUM(dies_pass_fp) / SUM(dies_tested)
  - name: Retest Rate
    expr: SUM(retest_rate * dies_tested) / SUM(dies_tested)
  - name: FPY Baseline 21d
    expr: AVG(fpy_baseline_21d)
$$;

CREATE OR REPLACE VIEW mv_stdf_failure_bins
COMMENT 'Weekly failure hard-bin counts by site and product.'
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: gold_bin_mix_weekly
dimensions:
  - name: Week Start
    expr: week_start
  - name: Site
    expr: site
  - name: Product
    expr: product
  - name: Hard Bin
    expr: hard_bin
measures:
  - name: Die Count
    expr: SUM(die_count)
$$;

CREATE OR REPLACE VIEW mv_stdf_param_drift
COMMENT 'Daily parametric drift (z-score vs pre-event baseline) and limit-breach rate.'
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: gold_param_drift_timeseries
dimensions:
  - name: Date
    expr: date
  - name: Site
    expr: site
  - name: Product
    expr: product
  - name: Parameter Code
    expr: param_code
measures:
  - name: Mean Z-Score
    expr: AVG(mean_z)
  - name: Limit Breach Rate
    expr: AVG(pct_limit_breach)
$$;

CREATE OR REPLACE VIEW mv_stdf_throughput
COMMENT 'Daily wafer-sort throughput (UPH) by site and product.'
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: gold_throughput_daily
dimensions:
  - name: Date
    expr: date
  - name: Site
    expr: site
  - name: Product
    expr: product
measures:
  - name: Average UPH
    expr: AVG(avg_units_per_hour)
  - name: Total Dies Tested
    expr: SUM(dies_tested)
$$;

CREATE OR REPLACE VIEW mv_stdf_business_impact
COMMENT 'Cumulative MX-7 lost dies and cost vs FPY baseline by site.'
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: gold_impact_cumulative
dimensions:
  - name: Date
    expr: date
  - name: Site
    expr: site
measures:
  - name: Cumulative Lost Dies
    expr: MAX(cumulative_lost_dies)
  - name: Cumulative Cost USD
    expr: MAX(cumulative_cost_usd)
$$;

CREATE OR REPLACE VIEW mv_stdf_equipment_changes
COMMENT 'Equipment, firmware, probe card and recipe change log.'
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: gold_change_log
dimensions:
  - name: Change Time
    expr: change_time
  - name: Date
    expr: date
  - name: Site
    expr: site
  - name: Tester ID
    expr: tester_id
  - name: Change Type
    expr: change_type
  - name: Scope
    expr: scope
  - name: ECR ID
    expr: ecr_id
  - name: Details
    expr: details
measures:
  - name: Change Count
    expr: COUNT(1)
$$;
