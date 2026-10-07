"""Wafer-map ML: anomaly detection + pattern classification.

Runs after transformations.sql and before wafer_patterns.sql.

1. Builds a feature matrix per wafer: the spatial features in silver_wafer_features, plus a
   radial fail-rate profile and rotation-invariant ring/sector fail rates from gold_wafer_map_dies.
2. Anomaly detector (unsupervised): an Isolation Forest over all wafers. anomaly_score is high when
   a wafer's map is unusual. The flag threshold is the score that best separates engineer-labelled
   'None' wafers from patterned ones.
3. Pattern classifier (supervised): trained on the engineer-reviewed wafers. Two candidates are
   compared with stratified 5-fold CV and the better one (macro F1) is refit on all labels.
4. Both are registered in Unity Catalog with alias @prod, re-loaded from the registry and used to
   score every wafer into gold_wafer_pattern_predictions. Metrics (including the rule-based baseline
   on the same wafers) go to gold_wafer_model_metrics.
"""
import argparse
import json
import logging
from datetime import datetime, timezone

import mlflow
import numpy as np
import pandas as pd
import sklearn
from mlflow.models import infer_signature
from mlflow.pyfunc import PythonModel
from mlflow.tracking import MlflowClient
from pyspark.sql import SparkSession
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest, RandomForestClassifier
from sklearn.metrics import (accuracy_score, f1_score, precision_recall_curve, recall_score,
                             roc_auc_score)
from sklearn.model_selection import StratifiedKFold, cross_val_predict

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('wafer_ml')

SEED = 7
SCALAR_FEATURES = [
    'fail_rate', 'center_fail_rate', 'inner_fail_rate', 'outer_fail_rate', 'edge_fail_rate',
    'edge_fail_angular_conc', 'inner_fail_angular_conc', 'spatial_chi2_ratio', 'n_clustered_fail',
    'cluster_excess', 'clustered_mean_r', 'clustered_elongation',
]
N_RADIAL_BINS = 10
# Rings outside the center disc, split into 8 angular sectors each. Sector fail rates are sorted
# within a ring so the features don't depend on where around the wafer a pattern sits.
RINGS = [(0.35, 0.65), (0.65, 0.82), (0.82, 1.01)]
N_SECTORS = 8


def component_features(fails):
    """Shape of the largest 8-connected blob of failing dies on one wafer.

    Scratches are long thin blobs, Loc a compact interior blob, rings and edges large curved ones.
    """
    cells = set(zip(fails['die_x'], fails['die_y']))
    r_by_cell = dict(zip(zip(fails['die_x'], fails['die_y']), fails['r_norm']))
    seen, sizes, best = set(), [], []
    for start in cells:
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            x, y = stack.pop()
            comp.append((x, y))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nb = (x + dx, y + dy)
                    if nb in cells and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
        sizes.append(len(comp))
        if len(comp) > len(best):
            best = comp
    out = {'cc_count_ge3': sum(1 for n in sizes if n >= 3), 'cc_largest_size': len(best),
           'cc_largest_frac': len(best) / max(len(cells), 1),
           'cc_largest_elongation': 0.0, 'cc_largest_length': 0.0, 'cc_largest_mean_r': 0.0}
    if len(best) >= 3:
        pts = np.array(best, dtype=float)
        eig = np.sort(np.linalg.eigvalsh(np.cov(pts.T)))[::-1]
        axis = np.linalg.eigh(np.cov(pts.T))[1][:, -1]
        proj = (pts - pts.mean(axis=0)) @ axis
        out['cc_largest_elongation'] = float(eig[0] / max(eig[1], 0.05))
        out['cc_largest_length'] = float(proj.max() - proj.min())
        out['cc_largest_mean_r'] = float(np.mean([r_by_cell[c] for c in best]))
    return out


class WaferPatternModel(PythonModel):
    """Returns the predicted pattern class and the classifier's probability for it."""

    def __init__(self, estimator, features):
        self.estimator = estimator
        self.features = features

    def predict(self, context, model_input, params=None):
        proba = self.estimator.predict_proba(model_input[self.features])
        idx = proba.argmax(axis=1)
        return pd.DataFrame({
            'pattern_class': self.estimator.classes_[idx],
            'pattern_confidence': proba[np.arange(len(idx)), idx].round(4),
        })


class WaferAnomalyModel(PythonModel):
    """Returns anomaly_score (higher = more unusual wafer map) and the thresholded flag."""

    def __init__(self, estimator, features, threshold):
        self.estimator = estimator
        self.features = features
        self.threshold = threshold

    def predict(self, context, model_input, params=None):
        score = -self.estimator.score_samples(model_input[self.features])
        return pd.DataFrame({
            'anomaly_score': score.round(4),
            'is_anomalous': score >= self.threshold,
        })


def build_features(spark, catalog, schema):
    base = spark.table(f'{catalog}.{schema}.silver_wafer_features').toPandas()

    radial = spark.sql(f"""
        SELECT wafer_id,
               LEAST({N_RADIAL_BINS - 1}, CAST(FLOOR(r_norm * {N_RADIAL_BINS}) AS INT)) AS rbin,
               CAST(AVG(CASE WHEN pass_flag THEN 0 ELSE 1 END) AS DOUBLE) AS fr
        FROM {catalog}.{schema}.gold_wafer_map_dies
        GROUP BY ALL""").toPandas()
    radial = radial.pivot(index='wafer_id', columns='rbin', values='fr')
    radial.columns = [f'radial_fr_{int(c)}' for c in radial.columns]

    ring_case = ' '.join(
        f'WHEN r_norm >= {lo} AND r_norm < {hi} THEN {i}' for i, (lo, hi) in enumerate(RINGS))
    sectors = spark.sql(f"""
        SELECT wafer_id,
               CASE {ring_case} END AS ring,
               LEAST({N_SECTORS - 1}, CAST(FLOOR((theta + PI()) / (2 * PI() / {N_SECTORS})) AS INT)) AS sector,
               CAST(AVG(CASE WHEN pass_flag THEN 0 ELSE 1 END) AS DOUBLE) AS fr
        FROM {catalog}.{schema}.gold_wafer_map_dies
        WHERE r_norm >= {RINGS[0][0]}
        GROUP BY ALL""").toPandas()
    rows = {}
    for (wafer_id, ring), g in sectors.groupby(['wafer_id', 'ring']):
        vals = np.sort(g['fr'].to_numpy(dtype=float))[::-1]
        vals = np.pad(vals, (0, N_SECTORS - len(vals)))
        r = rows.setdefault(wafer_id, {})
        for k, v in enumerate(vals):
            r[f'ring{ring}_sector_rank{k}'] = v
        # Max sector vs ring mean: high for a localized patch, ~1 for a full ring
        r[f'ring{ring}_peak_ratio'] = vals[0] / max(vals.mean(), 1e-3)
    sector_df = pd.DataFrame.from_dict(rows, orient='index')

    fails = spark.sql(f"""
        SELECT wafer_id, die_x, die_y, CAST(r_norm AS DOUBLE) AS r_norm
        FROM {catalog}.{schema}.gold_wafer_map_dies
        WHERE NOT pass_flag""").toPandas()
    cc_df = pd.DataFrame.from_dict(
        {w: component_features(g) for w, g in fails.groupby('wafer_id')}, orient='index')

    df = base.set_index('wafer_id').join(radial).join(sector_df).join(cc_df).reset_index()
    features = SCALAR_FEATURES + list(radial.columns) + list(sector_df.columns) + list(cc_df.columns)
    df[features] = df[features].astype(float).fillna(0.0)
    return df, features


def log_and_register(python_model, sample_x, full_name, extra_params, metrics):
    sample_y = python_model.predict(None, sample_x)
    with mlflow.start_run(run_name=full_name.split('.')[-1]):
        mlflow.log_params(extra_params)
        mlflow.log_metrics(metrics)
        info = mlflow.pyfunc.log_model(
            name='model',
            python_model=python_model,
            signature=infer_signature(sample_x, sample_y),
            input_example=sample_x.head(3),
            registered_model_name=full_name,
            pip_requirements=[f'scikit-learn=={sklearn.__version__}', f'pandas=={pd.__version__}',
                              f'numpy=={np.__version__}'],
        )
    version = info.registered_model_version
    MlflowClient(registry_uri='databricks-uc').set_registered_model_alias(full_name, 'prod', version)
    logger.info('Registered %s version %s as @prod', full_name, version)
    return str(version)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--schema', required=True)
    parser.add_argument('--experiment', required=True)
    args = parser.parse_args()

    spark = SparkSession.builder.getOrCreate()
    mlflow.set_registry_uri('databricks-uc')
    mlflow.set_experiment(args.experiment)
    classifier_name = f'{args.catalog}.{args.schema}.wafer_pattern_classifier'
    anomaly_name = f'{args.catalog}.{args.schema}.wafer_anomaly_detector'

    df, features = build_features(spark, args.catalog, args.schema)
    labeled = df[df['reviewed_pattern'].notna() & (df['reviewed_pattern'] != '')].reset_index(drop=True)
    X_all, X_lab, y_lab = df[features], labeled[features], labeled['reviewed_pattern']
    logger.info('Wafers: %d, labelled: %d, features: %d', len(df), len(labeled), len(features))
    logger.info('Label counts: %s', y_lab.value_counts().to_dict())

    metrics_rows = []

    def add_metric(model, metric, value, version=None, n=None):
        metrics_rows.append((model, metric, float(value), version, n))

    # ---- Pattern classifier: compare candidates with out-of-fold predictions ----
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    candidates = {
        'hist_gradient_boosting': HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0,
            class_weight='balanced', random_state=SEED),
        'random_forest': RandomForestClassifier(
            n_estimators=500, min_samples_leaf=1, class_weight='balanced_subsample',
            random_state=SEED, n_jobs=-1),
    }
    results = {}
    for name, est in candidates.items():
        oof = cross_val_predict(est, X_lab, y_lab, cv=cv)
        results[name] = {
            'oof': oof,
            'accuracy': accuracy_score(y_lab, oof),
            'macro_f1': f1_score(y_lab, oof, average='macro'),
        }
        logger.info('%s: CV accuracy %.4f, macro F1 %.4f', name,
                    results[name]['accuracy'], results[name]['macro_f1'])
    best_name = max(results, key=lambda k: results[k]['macro_f1'])
    best = results[best_name]
    logger.info('Selected classifier: %s', best_name)

    rule = labeled['rule_pattern_class']
    classes = sorted(y_lab.unique())
    clf_recall = recall_score(y_lab, best['oof'], labels=classes, average=None, zero_division=0)
    rule_recall = recall_score(y_lab, rule, labels=classes, average=None, zero_division=0)
    for c, mr, rr in zip(classes, clf_recall, rule_recall):
        n = int((y_lab == c).sum())
        logger.info('  %-10s n=%3d  model recall %.3f  rules recall %.3f', c, n, mr, rr)

    final_clf = candidates[best_name].fit(X_lab, y_lab)
    clf_version = log_and_register(
        WaferPatternModel(final_clf, features), X_lab, classifier_name,
        {'estimator': best_name, 'n_features': len(features), 'n_labeled': len(labeled), 'cv_folds': 5},
        {'cv_accuracy': best['accuracy'], 'cv_macro_f1': best['macro_f1']})

    add_metric('pattern_classifier', 'cv_accuracy', best['accuracy'], clf_version, len(labeled))
    add_metric('pattern_classifier', 'cv_macro_f1', best['macro_f1'], clf_version, len(labeled))
    add_metric('rules_baseline', 'accuracy', accuracy_score(y_lab, rule), None, len(labeled))
    add_metric('rules_baseline', 'macro_f1', f1_score(y_lab, rule, average='macro'), None, len(labeled))
    for c, mr, rr in zip(classes, clf_recall, rule_recall):
        n = int((y_lab == c).sum())
        add_metric('pattern_classifier', f'cv_recall_{c}', mr, clf_version, n)
        add_metric('rules_baseline', f'recall_{c}', rr, None, n)

    # ---- Anomaly detector: unsupervised, threshold calibrated on labels ----
    iforest = IsolationForest(n_estimators=500, max_samples=256, random_state=SEED).fit(X_all)
    lab_score = -iforest.score_samples(X_lab)
    is_patterned = (y_lab != 'None').to_numpy()
    auc = roc_auc_score(is_patterned, lab_score)
    prec, rec, thr = precision_recall_curve(is_patterned, lab_score)
    f1 = 2 * prec * rec / np.maximum(prec + rec, 1e-9)
    k = int(np.argmax(f1[:-1]))
    threshold = float(thr[k])
    logger.info('Anomaly detector: ROC AUC %.4f; threshold %.4f gives precision %.3f recall %.3f',
                auc, threshold, prec[k], rec[k])
    anomaly_version = log_and_register(
        WaferAnomalyModel(iforest, features, threshold), X_lab, anomaly_name,
        {'estimator': 'isolation_forest', 'n_estimators': 500, 'n_features': len(features),
         'n_train_wafers': len(df), 'threshold': threshold},
        {'roc_auc_vs_labels': auc, 'precision_at_threshold': float(prec[k]),
         'recall_at_threshold': float(rec[k])})
    add_metric('anomaly_detector', 'roc_auc', auc, anomaly_version, len(labeled))
    add_metric('anomaly_detector', 'precision', prec[k], anomaly_version, len(labeled))
    add_metric('anomaly_detector', 'recall', rec[k], anomaly_version, len(labeled))
    add_metric('anomaly_detector', 'threshold', threshold, anomaly_version, None)

    # ---- Score every wafer with the registered @prod models ----
    clf_prod = mlflow.pyfunc.load_model(f'models:/{classifier_name}@prod')
    anomaly_prod = mlflow.pyfunc.load_model(f'models:/{anomaly_name}@prod')
    scored = pd.concat([
        df[['wafer_id']].reset_index(drop=True),
        clf_prod.predict(X_all).reset_index(drop=True),
        anomaly_prod.predict(X_all).reset_index(drop=True),
    ], axis=1)
    oof = pd.DataFrame({'wafer_id': labeled['wafer_id'], 'cv_predicted_pattern': best['oof']})
    scored = scored.merge(oof, on='wafer_id', how='left')
    scored['classifier_version'] = clf_version
    scored['anomaly_detector_version'] = anomaly_version
    scored['scored_at'] = datetime.now(timezone.utc).replace(tzinfo=None)
    add_metric('anomaly_detector', 'wafers_flagged', scored['is_anomalous'].sum(), anomaly_version, len(scored))
    logger.info('Scored %d wafers; %d flagged anomalous', len(scored), int(scored['is_anomalous'].sum()))
    logger.info('Predicted pattern counts: %s', scored['pattern_class'].value_counts().to_dict())

    pred_table = f'{args.catalog}.{args.schema}.gold_wafer_pattern_predictions'
    spark.createDataFrame(scored).write.mode('overwrite').option('overwriteSchema', 'true').saveAsTable(pred_table)
    spark.sql(f"""COMMENT ON TABLE {pred_table} IS 'One row per wafer, scored by the @prod Unity Catalog models:
        pattern_class and pattern_confidence from wafer_pattern_classifier; anomaly_score (higher = more
        unusual map) and is_anomalous from wafer_anomaly_detector. cv_predicted_pattern is the out-of-fold
        cross-validation prediction for engineer-reviewed wafers, used for honest evaluation.'""")

    metrics_table = f'{args.catalog}.{args.schema}.gold_wafer_model_metrics'
    metrics_df = pd.DataFrame(metrics_rows, columns=['model', 'metric', 'value', 'model_version', 'n_wafers'])
    metrics_df['value'] = metrics_df['value'].round(4)
    metrics_df['evaluated_at'] = scored['scored_at'].iloc[0]
    spark.createDataFrame(metrics_df).write.mode('overwrite').option('overwriteSchema', 'true').saveAsTable(metrics_table)
    spark.sql(f"""COMMENT ON TABLE {metrics_table} IS 'Evaluation of the wafer-map models against
        engineer-reviewed wafers: pattern_classifier (5-fold out-of-fold CV), rules_baseline (the former
        rule-based classifier on the same wafers) and anomaly_detector (ROC AUC, precision/recall at the
        flag threshold, wafers flagged).'""")

    summary = {
        'classifier': best_name, 'classifier_version': clf_version,
        'cv_accuracy': round(best['accuracy'], 4), 'cv_macro_f1': round(best['macro_f1'], 4),
        'rules_accuracy': round(accuracy_score(y_lab, rule), 4),
        'anomaly_detector_version': anomaly_version, 'anomaly_roc_auc': round(auc, 4),
        'wafers_scored': len(scored), 'wafers_flagged': int(scored['is_anomalous'].sum()),
    }
    logger.info('Summary: %s', json.dumps(summary))


if __name__ == '__main__':
    main()
