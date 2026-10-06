-- ==================================================================================
-- STDF raw ingestion - Lakeflow Spark Declarative Pipeline
-- Auto Loader streams the parquet files written by generate_data.py from the raw_data
-- volume into bronze streaming tables, with data-quality expectations on every feed.
-- Raw path comes from the pipeline configuration key `stdf.raw_path`.
-- ==================================================================================

-- PRR: one row per die per wafer-sort session
CREATE OR REFRESH STREAMING TABLE raw_stdf_raw_prr_parts (
  CONSTRAINT valid_part_id EXPECT (part_id IS NOT NULL AND wafer_id IS NOT NULL AND lot_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_die_coords EXPECT (x_coord >= 0 AND y_coord >= 0) ON VIOLATION DROP ROW,
  CONSTRAINT known_site EXPECT (site IN ('AUS', 'HSC', 'PNG')),
  CONSTRAINT known_hard_bin EXPECT (hard_bin RLIKE '^HB_[0-9]{3}$')
)
COMMENT 'Bronze STDF PRR part results (die-level pass/fail, bins, X/Y coordinates, equipment) ingested with Auto Loader.'
AS SELECT
  *,
  _metadata.file_path AS _source_file,
  current_timestamp() AS _ingested_at
FROM STREAM read_files('${stdf.raw_path}/raw_stdf_raw_prr_parts', format => 'parquet');

-- PTR: parametric test results
CREATE OR REFRESH STREAMING TABLE raw_stdf_raw_ptr_params (
  CONSTRAINT valid_part_id EXPECT (part_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT known_param EXPECT (param_code IN ('PT_0210', 'PT_0217', 'PT_0103', 'PT_0301')),
  CONSTRAINT finite_value EXPECT (value IS NOT NULL AND NOT isnan(value)) ON VIOLATION DROP ROW
)
COMMENT 'Bronze STDF PTR parametric results (contact resistance, IDDQ, Vth) ingested with Auto Loader.'
AS SELECT
  *,
  _metadata.file_path AS _source_file,
  current_timestamp() AS _ingested_at
FROM STREAM read_files('${stdf.raw_path}/raw_stdf_raw_ptr_params', format => 'parquet');

-- Lot / wafer master
CREATE OR REFRESH STREAMING TABLE raw_stdf_raw_lot_wafer_master (
  CONSTRAINT valid_wafer EXPECT (wafer_id IS NOT NULL AND lot_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT positive_die_count EXPECT (dies_per_wafer_expected > 0)
)
COMMENT 'Bronze lot and wafer master (product, foundry, expected dies, start date) ingested with Auto Loader.'
AS SELECT
  *,
  _metadata.file_path AS _source_file,
  current_timestamp() AS _ingested_at
FROM STREAM read_files('${stdf.raw_path}/raw_stdf_raw_lot_wafer_master', format => 'parquet');

-- Equipment change log
CREATE OR REFRESH STREAMING TABLE raw_stdf_raw_equip_change_log (
  CONSTRAINT valid_change EXPECT (change_time IS NOT NULL AND tester_id IS NOT NULL) ON VIOLATION DROP ROW
)
COMMENT 'Bronze equipment / firmware / probe card change log ingested with Auto Loader.'
AS SELECT
  *,
  _metadata.file_path AS _source_file,
  current_timestamp() AS _ingested_at
FROM STREAM read_files('${stdf.raw_path}/raw_stdf_raw_equip_change_log', format => 'parquet');

-- Yield-engineer wafer review labels
CREATE OR REFRESH STREAMING TABLE raw_stdf_raw_wafer_review_labels (
  CONSTRAINT valid_label EXPECT (wafer_id IS NOT NULL AND reviewed_pattern IS NOT NULL) ON VIOLATION DROP ROW
)
COMMENT 'Bronze yield-engineer wafer-map review labels ingested with Auto Loader.'
AS SELECT
  *,
  _metadata.file_path AS _source_file,
  current_timestamp() AS _ingested_at
FROM STREAM read_files('${stdf.raw_path}/raw_stdf_raw_wafer_review_labels', format => 'parquet');
