-- ==================================================================================
-- STDF Manufacturing Operations Demo - Wafer-map pattern gold layer
-- Target: <catalog>.<schema> (SQL task parameters `catalog` / `schema`, set in databricks.yml)
-- Purpose: Combine wafer sort context, spatial features and the ML model output
--          (gold_wafer_pattern_predictions, written by wafer_ml.py) into the wafer-pattern gold
--          tables and metric view used by the dashboard, Genie, Lakebase and the app.
-- ==================================================================================

USE CATALOG IDENTIFIER(:catalog);
USE SCHEMA IDENTIFIER(:schema);

-- Gold: Wafer patterns (sort context + spatial features + ML pattern class and anomaly score)
CREATE OR REPLACE TABLE gold_wafer_patterns AS
SELECT
  s.wafer_id,
  s.lot_id,
  s.site,
  s.product,
  s.foundry,
  s.tester_id,
  s.probe_card_id,
  s.handler_id,
  s.program_version,
  s.sort_start,
  s.sort_date,
  s.week_start,
  s.dies_tested,
  s.dies_pass,
  s.dies_retested,
  s.wafer_yield,
  p.pattern_class,
  CASE
    WHEN p.pattern_class IN ('Edge-Ring', 'Edge-Loc') THEN 'Probe contact / edge process (planarity, bevel etch)'
    WHEN p.pattern_class = 'Center' THEN 'Center process non-uniformity (CMP, implant, deposition)'
    WHEN p.pattern_class = 'Donut' THEN 'Radial process ring (spin coat, thermal)'
    WHEN p.pattern_class = 'Scratch' THEN 'Mechanical handling scratch'
    WHEN p.pattern_class = 'Loc' THEN 'Localized defect cluster (particle, reticle)'
    WHEN p.pattern_class = 'Random' THEN 'Elevated random defectivity'
    WHEN p.pattern_class = 'Near-full' THEN 'Gross wafer failure'
    ELSE 'No systematic pattern'
  END AS likely_cause,
  p.pattern_confidence,
  p.anomaly_score,
  p.is_anomalous,
  f.rule_pattern_class,
  f.fail_rate,
  f.center_fail_rate,
  f.inner_fail_rate,
  f.outer_fail_rate,
  f.edge_fail_rate,
  f.edge_fail_angular_conc,
  f.inner_fail_angular_conc,
  f.spatial_chi2_ratio,
  f.n_clustered_fail,
  f.cluster_excess,
  f.clustered_mean_r,
  f.clustered_elongation,
  f.reviewed_pattern,
  p.classifier_version,
  p.anomaly_detector_version
FROM silver_wafer_sort_sessions s
JOIN silver_wafer_features f ON s.wafer_id = f.wafer_id
JOIN gold_wafer_pattern_predictions p ON s.wafer_id = p.wafer_id;
ALTER TABLE gold_wafer_patterns SET TBLPROPERTIES ('comment' = 'One row per wafer: sort context (lot, site, product, tester, probe card, handler, date), wafer yield, spatial features, and the ML output: pattern_class (None, Random, Center, Donut, Edge-Ring, Edge-Loc, Loc, Scratch) with pattern_confidence from the wafer_pattern_classifier model, and anomaly_score / is_anomalous from the wafer_anomaly_detector model, plus likely_cause. rule_pattern_class is the former rule-based class, kept as a baseline. reviewed_pattern holds the engineer label when the wafer was reviewed.');

-- Gold: Wafer pattern mix weekly
CREATE OR REPLACE TABLE gold_wafer_pattern_weekly AS
SELECT
  week_start,
  site,
  product,
  pattern_class,
  COUNT(*) AS wafer_count,
  COUNT_IF(is_anomalous) AS anomalous_wafer_count,
  ROUND(AVG(wafer_yield), 4) AS avg_wafer_yield,
  SUM(dies_tested - dies_pass) AS dies_failed
FROM gold_wafer_patterns
GROUP BY week_start, site, product, pattern_class;
ALTER TABLE gold_wafer_pattern_weekly SET TBLPROPERTIES ('comment' = 'Weekly wafer counts, anomalous wafer counts, average wafer yield and failed dies by site/product/pattern_class. Shows the Edge-Ring surge at AUS MX-7 during 2025-08-18..2025-08-27.');

-- Gold: Classifier agreement with engineer review labels (confusion matrix).
-- Uses the out-of-fold cross-validation prediction, so every reviewed wafer is scored by a model
-- that never saw its label.
CREATE OR REPLACE TABLE gold_wafer_pattern_eval AS
SELECT
  f.reviewed_pattern,
  p.cv_predicted_pattern AS predicted_pattern,
  COUNT(*) AS wafer_count,
  f.reviewed_pattern = p.cv_predicted_pattern AS is_match
FROM silver_wafer_features f
JOIN gold_wafer_pattern_predictions p ON f.wafer_id = p.wafer_id
WHERE f.reviewed_pattern IS NOT NULL
GROUP BY f.reviewed_pattern, p.cv_predicted_pattern;
ALTER TABLE gold_wafer_pattern_eval SET TBLPROPERTIES ('comment' = 'Confusion matrix of the ML pattern classifier (5-fold out-of-fold predictions) vs yield-engineer reviewed_pattern for reviewed wafers. Agreement = SUM(wafer_count) FILTER (is_match) / SUM(wafer_count). gold_wafer_model_metrics compares this with the rule-based baseline.');

CREATE OR REPLACE VIEW mv_stdf_wafer_patterns
COMMENT 'Wafer-map pattern classification (ML classifier) and anomaly scores per wafer sort session.'
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: gold_wafer_patterns
dimensions:
  - name: Sort Date
    expr: sort_date
  - name: Week Start
    expr: week_start
  - name: Site
    expr: site
  - name: Product
    expr: product
  - name: Foundry
    expr: foundry
  - name: Lot ID
    expr: lot_id
  - name: Wafer ID
    expr: wafer_id
  - name: Tester ID
    expr: tester_id
  - name: Probe Card ID
    expr: probe_card_id
  - name: Handler ID
    expr: handler_id
  - name: Pattern Class
    expr: pattern_class
  - name: Likely Cause
    expr: likely_cause
  - name: Is Anomalous
    expr: is_anomalous
measures:
  - name: Wafer Count
    expr: COUNT(1)
  - name: Patterned Wafer Count
    expr: COUNT_IF(pattern_class NOT IN ('None', 'Random'))
  - name: Patterned Wafer Rate
    expr: COUNT_IF(pattern_class NOT IN ('None', 'Random')) / COUNT(1)
  - name: Average Wafer Yield
    expr: AVG(wafer_yield)
  - name: Dies Tested
    expr: SUM(dies_tested)
  - name: Dies Failed
    expr: SUM(dies_tested - dies_pass)
  - name: Average Edge Fail Rate
    expr: AVG(edge_fail_rate)
  - name: Anomalous Wafer Count
    expr: COUNT_IF(is_anomalous)
  - name: Anomalous Wafer Rate
    expr: COUNT_IF(is_anomalous) / COUNT(1)
  - name: Average Anomaly Score
    expr: AVG(anomaly_score)
  - name: Average Pattern Confidence
    expr: AVG(pattern_confidence)
$$;
