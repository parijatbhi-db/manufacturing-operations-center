# generate_data.py for the STDF Manufacturing Operations demo
# RAW synthetic datasets that follow demo_story.json
# - stdf_prr_parts           (one row per die per wafer-sort session, with X/Y die coordinates)
# - stdf_ptr_params
# - equip_change_log
# - lot_wafer_master
# - wafer_review_labels      (yield-engineer pattern labels for a sample of wafers)
#
# Contracts:
# - Schema fidelity (columns/dtypes)
# - Referential integrity between PRR and PTR (part_id, wafer_id, lot_id)
# - Every wafer is probed in a single sort session on one tester/probe card/handler, so each
#   wafer has a complete die map that wafer-map pattern classification can run on
# - Event visibility: AUS MX-7 FPY drop 2025-08-18..2025-08-27 on TST-AUS-03..05 (probe card
#   PC-AUS-447 Rev C) shows up as an Edge-Ring pattern with HB_021/PT_0210 shifts, retest spike
# - Seasonality: weekday/weekend volume, business-hour timestamps
# - Datetime naive, floored to ms

import argparse
import os
import random

import numpy as np
import pandas as pd
from faker import Faker

from utils import save_to_parquet


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--catalog', default='parijat_demos')
    parser.add_argument('--schema', default='mfg_ops')
    parser.add_argument('--volume', default='raw_data')
    parser.add_argument('--local', action='store_true', help='Write parquet to ./data instead of a UC volume')
    args, _ = parser.parse_known_args()
    return args


# ===============================
# === REPRO & GLOBAL WINDOWS
# ===============================
SEED = 42
np.random.seed(SEED)
random.seed(SEED)
fake = Faker()
Faker.seed(SEED)

# Story window
RANGE_START = pd.Timestamp('2025-06-15').floor('ms')
RANGE_END = pd.Timestamp('2025-10-05').floor('ms')
EVENT_START = pd.Timestamp('2025-08-18 07:30').floor('ms')  # trigger
EVENT_MIN = pd.Timestamp('2025-08-20').floor('ms')          # min FPY
EVENT_END = pd.Timestamp('2025-08-27 23:59').floor('ms')    # end of visible degradation
RECOVERY_TIME = pd.Timestamp('2025-08-24 22:15').floor('ms')
RESIDUAL_END = pd.Timestamp('2025-09-01 23:59').floor('ms')

DAYS = pd.date_range(RANGE_START.normalize(), RANGE_END.normalize(), freq='D')
DAYS = pd.to_datetime(DAYS, utc=False).tz_localize(None)
HOURS = np.arange(24)

# ===============================
# === STATIC ENUMS / LOOKUPS
# ===============================
sites = ['AUS', 'HSC', 'PNG']
products = ['MX-5', 'MX-7', 'RF-22']
foundries = ['F10', 'F12']

# Testers per site
TESTERS = {
    'AUS': [f'TST-AUS-{i:02d}' for i in range(1, 9)],
    'HSC': [f'TST-HSC-{i:02d}' for i in range(1, 7)],
    'PNG': [f'TST-PNG-{i:02d}' for i in range(1, 6)],
}

# Probe cards / handlers
PROBE_CARDS = {
    'AUS': ['PC-AUS-447', 'PC-AUS-389', 'PC-AUS-402'],
    'HSC': ['PC-HSC-210', 'PC-HSC-315'],
    'PNG': ['PC-PNG-118', 'PC-PNG-119'],
}
HANDLERS = {
    'AUS': ['HND-AUS-10', 'HND-AUS-11', 'HND-AUS-12'],
    'HSC': ['HND-HSC-07', 'HND-HSC-08'],
    'PNG': ['HND-PNG-03', 'HND-PNG-04'],
}

# Soft/Hard bins (strings)
SOFT_PASS = 'SB_001'
HARD_PASS_PRIMARY = 'HB_001'
HARD_PASS_SECOND = 'HB_002'
FAIL_BINS = ['HB_014', 'HB_021', 'HB_007', 'HB_032']  # include IDDQ, Open/Short, others
SOFT_FAILS = ['SB_014', 'SB_021', 'SB_007', 'SB_032']

# Param codes
PARAMS = ['PT_0210', 'PT_0217', 'PT_0103', 'PT_0301']  # Contact R, IDDQ, Vth, extra


# Testers that received probe card PC-AUS-447 Rev C + handler firmware HF-3.2.1 on 2025-08-18
AFFECTED_TESTERS = ['TST-AUS-03', 'TST-AUS-04', 'TST-AUS-05']
AFFECTED_PROBE_CARD = 'PC-AUS-447'
PEAK_SEVERITY = 0.36  # edge-ring kill amplitude at the height of the incident
# Below this amplitude an edge ring is not visible to a reviewing engineer
VISIBLE_AMPLITUDE = 0.08

# ===============================
# === WAFER GEOMETRY & PATTERNS
# ===============================

# Die-grid radius (in dies) per product; dies are the grid cells inside the wafer circle
DIE_GRID_RADIUS = {'MX-5': 12, 'MX-7': 13, 'RF-22': 11}
# Probe index time per die (seconds)
INDEX_TIME_S = {'MX-5': 5.0, 'MX-7': 5.5, 'RF-22': 6.5}
WAFERS_PER_LOT = 25

# Wafer-map pattern classes (WM-811K taxonomy)
PATTERNS = ['None', 'Random', 'Center', 'Donut', 'Edge-Ring', 'Edge-Loc', 'Loc', 'Scratch']
BASELINE_PATTERN_MIX = [0.72, 0.05, 0.05, 0.03, 0.03, 0.05, 0.04, 0.03]
# Dominant hard bin for dies killed by each spatial mechanism
PATTERN_BIN = {
    'Center': 'HB_014',     # IDDQ leakage
    'Donut': 'HB_032',      # parametric / Vth
    'Edge-Ring': 'HB_021',  # open/short (contact)
    'Edge-Loc': 'HB_021',
    'Loc': 'HB_007',        # functional
    'Scratch': 'HB_007',
}
# Approximate mean yield loss added by the baseline pattern mix; subtracted from the random
# defect rate so overall FPY still lands on FPY_BASE
PATTERN_LOSS_ALLOWANCE = 0.012


def die_grid(product: str):
    """Return (x, y, r_norm, theta) arrays for all dies on the product's wafer, in serpentine probe order."""
    R = DIE_GRID_RADIUS[product]
    xs, ys = [], []
    for y in range(0, 2 * R + 1):
        row = range(0, 2 * R + 1) if y % 2 == 0 else range(2 * R, -1, -1)
        for x in row:
            if (x - R) ** 2 + (y - R) ** 2 <= R ** 2:
                xs.append(x)
                ys.append(y)
    x = np.array(xs)
    y = np.array(ys)
    dx, dy = x - R, y - R
    r_norm = np.sqrt(dx ** 2 + dy ** 2) / R
    theta = np.arctan2(dy, dx)
    return x, y, r_norm, theta


DIE_GRIDS = {p: die_grid(p) for p in DIE_GRID_RADIUS}


def pattern_shape(pattern: str, x, y, r_norm, theta, R: int) -> np.ndarray:
    """Spatial kill-probability shape in [0, 1] for a pattern on one wafer."""
    if pattern == 'Center':
        return np.exp(-(r_norm / np.random.uniform(0.22, 0.30)) ** 2)
    if pattern == 'Donut':
        r0 = np.random.uniform(0.45, 0.60)
        return np.exp(-((r_norm - r0) / 0.09) ** 2)
    if pattern == 'Edge-Ring':
        return 1.0 / (1.0 + np.exp(-(r_norm - 0.84) / 0.03))
    if pattern == 'Edge-Loc':
        t0 = np.random.uniform(-np.pi, np.pi)
        dtheta = np.angle(np.exp(1j * (theta - t0)))
        return (1.0 / (1.0 + np.exp(-(r_norm - 0.72) / 0.04))) * np.exp(-(dtheta / 0.45) ** 2)
    if pattern == 'Loc':
        rc = np.random.uniform(0.25, 0.60) * R
        tc = np.random.uniform(-np.pi, np.pi)
        cx, cy = R + rc * np.cos(tc), R + rc * np.sin(tc)
        sig = np.random.uniform(0.10, 0.14) * R
        return np.exp(-(((x - cx) ** 2 + (y - cy) ** 2) / (2 * sig ** 2)))
    if pattern == 'Scratch':
        # Line segment through a random chord of the wafer
        ang = np.random.uniform(0, np.pi)
        off = np.random.uniform(-0.35, 0.35) * R
        nx, ny = -np.sin(ang), np.cos(ang)
        dist_line = np.abs((x - R) * nx + (y - R) * ny - off)
        along = (x - R) * np.cos(ang) + (y - R) * np.sin(ang)
        half_len = np.random.uniform(0.45, 0.80) * R
        s0 = np.random.uniform(-0.3, 0.3) * R
        on_seg = np.abs(along - s0) <= half_len
        return ((dist_line <= 0.55) & on_seg).astype(float)
    return np.zeros_like(r_norm)


PATTERN_AMPLITUDE = {
    'Center': (0.45, 0.70),
    'Donut': (0.30, 0.45),
    'Edge-Ring': (0.20, 0.30),
    'Edge-Loc': (0.55, 0.80),
    'Loc': (0.65, 0.90),
    'Scratch': (0.85, 0.95),
}

# ===============================
# === UTIL HELPERS
# ===============================

def business_hour_probs(weekday: bool) -> np.ndarray:
    if weekday:
        hp = np.array([
            0.005,0.005,0.005,0.005,0.005, 0.01,0.02,0.03,0.05,0.07,
            0.08,0.08,0.08,0.075,0.07, 0.06,0.05,0.05,0.045,0.035,
            0.03,0.02,0.015,0.01
        ])
    else:
        hp = np.array([
            0.005,0.005,0.005,0.005,0.01, 0.01,0.02,0.03,0.04,0.05,
            0.06,0.07,0.07,0.065,0.06, 0.055,0.05,0.05,0.055,0.06,
            0.045,0.03,0.02,0.015
        ])
    return hp / hp.sum()


def incident_severity(ts: pd.Timestamp) -> float:
    """Edge-ring kill amplitude on affected testers for an AUS MX-7 wafer probed at ts (0 = unaffected)."""
    if ts < EVENT_START or ts > RESIDUAL_END:
        return 0.0
    if ts < RECOVERY_TIME:
        return np.random.uniform(0.33, 0.39)
    if ts <= EVENT_END:
        # Linear recovery after the 2025-08-24 rollback
        frac = (ts - RECOVERY_TIME) / (EVENT_END - RECOVERY_TIME)
        return 0.36 - 0.26 * float(np.clip(frac, 0, 1))
    # Residual drift through 2025-09-01
    return np.random.uniform(0.02, 0.05)


# ===============================
# === WAFER SORT SCHEDULE + PRR (Part Results)
# ===============================

def generate_wafer_sort(daily_dies_mean: int = 5200):
    """Simulate wafer-sort sessions.

    Returns (lot_wafer_master, prr_parts, wafer_truth) where wafer_truth carries the injected
    pattern and incident flags (used for PTR shifts and review labels, never saved as-is).
    """
    print('Generating wafer sort sessions (lot_wafer_master + stdf_prr_parts)...')

    # Volume targets per site/product (MX-7 heavy at AUS)
    site_product_share = {
        ('AUS', 'MX-7'): 0.36,
        ('AUS', 'MX-5'): 0.18,
        ('AUS', 'RF-22'): 0.08,
        ('HSC', 'MX-7'): 0.16,
        ('HSC', 'MX-5'): 0.10,
        ('HSC', 'RF-22'): 0.04,
        ('PNG', 'MX-7'): 0.02,
        ('PNG', 'MX-5'): 0.02,
        ('PNG', 'RF-22'): 0.04,
    }
    total_share = sum(site_product_share.values())
    site_product_share = {k: v / total_share for k, v in site_product_share.items()}

    # Day-of-week volume multiplier
    DOW_MUL = {0: 1.05, 1: 1.10, 2: 1.12, 3: 1.08, 4: 0.96, 5: 0.72, 6: 0.68}

    # FPY baselines per site/product
    FPY_BASE = {
        ('AUS', 'MX-7'): 0.953,
        ('HSC', 'MX-7'): 0.967,
        ('AUS', 'MX-5'): 0.948,
        ('HSC', 'MX-5'): 0.955,
        ('PNG', 'MX-5'): 0.952,
        ('PNG', 'RF-22'): 0.939,
        ('HSC', 'RF-22'): 0.942,
        ('AUS', 'RF-22'): 0.940,
        ('PNG', 'MX-7'): 0.960,
    }
    # Share of failing dies that get re-probed
    RETEST_SHARE_BASE = 0.55

    # Hard bin mixes (baseline fail share for random defects)
    FAIL_MIX_BASE = np.array([0.40, 0.35, 0.15, 0.10])  # HB_014, HB_021, HB_007, HB_032
    SOFT_FOR_HARD = dict(zip(FAIL_BINS, SOFT_FAILS))

    # Lot bookkeeping: open lot per (site, product, foundry) with remaining wafer slots
    lot_rows = []
    lot_seq = {}
    open_lots = {}
    # MX-7 focus lots from F12 (W27..W29) are queued for AUS during the incident window
    focus_queue = []
    for wlot in ['W27', 'W28', 'W29']:
        lot_id = f'MX7-F12-{wlot}'
        n_wafers = np.random.randint(12, 15)
        start_date = (pd.Timestamp('2025-08-10') + pd.Timedelta(days=np.random.randint(-5, 5))).normalize()
        for wi in range(1, n_wafers + 1):
            focus_queue.append((lot_id, f'{lot_id}-W{wi:02d}', 'F12', start_date))

    def next_wafer(site, prod, day):
        if site == 'AUS' and prod == 'MX-7' and EVENT_START.normalize() <= day <= EVENT_END.normalize() and focus_queue:
            return focus_queue.pop(0)
        fnd = 'F12' if np.random.rand() < 0.5 else 'F10'
        key = (site, prod, fnd)
        lot = open_lots.get(key)
        if lot is None or lot['next'] > lot['n']:
            seq = lot_seq.get((prod, fnd), 0) + 1
            lot_seq[(prod, fnd)] = seq
            lot = {
                'lot_id': f"{prod.replace('-', '')}-{fnd}-{site[0]}{seq:03d}",
                'n': WAFERS_PER_LOT,
                'next': 1,
                'start_date': (day - pd.Timedelta(days=np.random.randint(5, 15))).normalize(),
            }
            open_lots[key] = lot
        wafer_id = f"{lot['lot_id']}-W{lot['next']:02d}"
        lot['next'] += 1
        return lot['lot_id'], wafer_id, fnd, lot['start_date']

    weekday_hp = business_hour_probs(True)
    weekend_hp = business_hour_probs(False)
    program_versions = ['TP-v1.8.3', 'TP-v1.8.4', 'TP-v1.9.0', 'TP-v2.0.1']

    prr_chunks = []
    truth_rows = []
    total_days = len(DAYS)
    progress_interval = max(1, total_days // 10)

    for i, d in enumerate(DAYS):
        if (i + 1) % progress_interval == 0 or i == 0:
            print(f"  Days: {((i + 1) / total_days) * 100:.0f}% ({i + 1:,}/{total_days:,})")
        day = pd.Timestamp(d).normalize()
        dow_mul = DOW_MUL.get(day.weekday(), 1.0)
        daily_dies = max(2000, int(np.random.normal(daily_dies_mean, 500))) * dow_mul
        hp = weekday_hp if day.weekday() < 5 else weekend_hp
        in_event_day = EVENT_START.normalize() <= day <= EVENT_END.normalize()

        for (site, prod), share in site_product_share.items():
            x, y, r_norm, theta = DIE_GRIDS[prod]
            n_dies = len(x)
            n_wafers = np.random.poisson(daily_dies * share / n_dies)
            fpy_base = FPY_BASE[(site, prod)]

            for _ in range(n_wafers):
                lot_id, wafer_id, fnd, lot_start = next_wafer(site, prod, day)
                start_ts = (day + pd.Timedelta(hours=int(np.random.choice(HOURS, p=hp)),
                                               minutes=int(np.random.randint(0, 60)))).floor('ms')

                # Equipment: during the incident AUS MX-7 is routed mostly to the re-carded testers
                if site == 'AUS' and prod == 'MX-7' and in_event_day and np.random.rand() < 0.85:
                    tester = np.random.choice(AFFECTED_TESTERS)
                else:
                    tester = np.random.choice(TESTERS[site])
                if tester in AFFECTED_TESTERS:
                    probe_card = AFFECTED_PROBE_CARD
                else:
                    probe_card = np.random.choice([pc for pc in PROBE_CARDS[site] if pc != AFFECTED_PROBE_CARD])
                handler = np.random.choice(HANDLERS[site])
                program = np.random.choice(program_versions, p=[0.35, 0.30, 0.25, 0.10])

                # Pattern + kill probability map
                severity = incident_severity(start_ts) if (site == 'AUS' and prod == 'MX-7' and tester in AFFECTED_TESTERS) else 0.0
                affected = severity > 0
                if affected:
                    pattern = 'Edge-Ring'
                    amp = severity
                else:
                    pattern = np.random.choice(PATTERNS, p=BASELINE_PATTERN_MIX)
                    amp = np.random.uniform(*PATTERN_AMPLITUDE[pattern]) if pattern in PATTERN_AMPLITUDE else 0.0

                p_random = max(0.005, np.random.normal(1.0 - fpy_base - PATTERN_LOSS_ALLOWANCE, 0.004))
                if pattern == 'Random':
                    p_random += np.random.uniform(0.05, 0.08)
                shape = pattern_shape(pattern, x, y, r_norm, theta, DIE_GRID_RADIUS[prod])
                p_pattern = amp * shape

                killed_random = np.random.rand(n_dies) < p_random
                killed_pattern = np.random.rand(n_dies) < p_pattern
                fail = killed_random | killed_pattern

                hard_bin = np.where(
                    np.random.rand(n_dies) < 0.85, HARD_PASS_PRIMARY, HARD_PASS_SECOND
                ).astype(object)
                random_bins = np.random.choice(FAIL_BINS, size=n_dies, p=FAIL_MIX_BASE)
                pattern_bin = PATTERN_BIN.get(pattern)
                hard_bin[killed_random] = random_bins[killed_random]
                if pattern_bin:
                    # Most pattern kills carry the mechanism's bin; a few land in other bins
                    pb = np.where(np.random.rand(n_dies) < 0.85, pattern_bin, random_bins)
                    hard_bin[killed_pattern] = pb[killed_pattern]
                soft_bin = np.array([SOFT_FOR_HARD.get(b, SOFT_PASS) for b in hard_bin], dtype=object)

                # Retest: a share of failing dies is re-probed (contact fails much more often)
                retest_share = RETEST_SHARE_BASE + 0.30 * min(1.0, severity / PEAK_SEVERITY)
                retest_flag = fail & (np.random.rand(n_dies) < retest_share)

                # Probe timestamps: serpentine order, slower indexing on HF-3.2.1 handlers
                index_s = INDEX_TIME_S[prod] * (1.25 if affected and start_ts < RECOVERY_TIME else 1.0)
                offsets = np.cumsum(np.random.normal(index_s, 0.3, size=n_dies).clip(1.0))
                ts = start_ts + pd.to_timedelta(offsets, unit='s')

                prefix = prod.replace('-', '')
                xy = np.char.add(np.char.add('X', np.char.zfill(x.astype(str), 3)),
                                 np.char.add('Y', np.char.zfill(y.astype(str), 3)))
                part_ids = np.char.add(f'{prefix}-{wafer_id}-', xy)
                part_ids = np.where(retest_flag, np.char.add(part_ids, '-R2'), part_ids).astype(object)

                prr_chunks.append(pd.DataFrame({
                    'part_id': part_ids,
                    'site': site,
                    'tester_id': tester,
                    'wafer_id': wafer_id,
                    'lot_id': lot_id,
                    'foundry': fnd,
                    'product': prod,
                    'timestamp': ts,
                    'x_coord': x.astype(int),
                    'y_coord': y.astype(int),
                    'soft_bin': soft_bin,
                    'hard_bin': hard_bin,
                    'pass_flag': ~fail,
                    'retest_flag': retest_flag,
                    'program_version': program,
                    'probe_card_id': probe_card,
                    'handler_id': handler,
                    '_r_norm': r_norm,
                    '_affected': affected,
                    '_severity': severity,
                }))
                truth_rows.append({
                    'wafer_id': wafer_id,
                    'site': site,
                    'product': prod,
                    'tester_id': tester,
                    'start_ts': start_ts,
                    'injected_pattern': pattern,
                    'amplitude': amp,
                    'affected': affected,
                })
                lot_rows.append({
                    'lot_id': lot_id,
                    'wafer_id': wafer_id,
                    'product': prod,
                    'foundry': fnd,
                    'dies_per_wafer_expected': n_dies,
                    'start_date': lot_start.floor('ms'),
                    'site_planned': site,
                })

    prr = pd.concat(prr_chunks, ignore_index=True)
    prr['timestamp'] = pd.to_datetime(prr['timestamp'], errors='coerce').dt.floor('ms')
    lot_df = pd.DataFrame(lot_rows)
    lot_df['start_date'] = pd.to_datetime(lot_df['start_date'], errors='coerce').dt.floor('ms')
    truth = pd.DataFrame(truth_rows)
    print(f'lot_wafer_master rows: {len(lot_df):,} | stdf_prr_parts rows: {len(prr):,}')
    return lot_df, prr, truth


# ===============================
# === WAFER REVIEW LABELS
# ===============================

def generate_wafer_review_labels(truth: pd.DataFrame, sample_frac: float = 0.4) -> pd.DataFrame:
    """Yield-engineer visual review labels for a sample of wafers (ground truth for classifier QA)."""
    print('Generating wafer_review_labels...')
    reviewers = [fake.unique.first_name() + ' ' + fake.last_name() for _ in range(6)]
    # Engineers always review the incident wafers; the rest is a random sample
    sample = truth[truth['affected'] | (np.random.rand(len(truth)) < sample_frac)].copy()
    labels = pd.DataFrame({
        'wafer_id': sample['wafer_id'].values,
        'reviewed_pattern': np.where(
            sample['affected'] & (sample['amplitude'] < VISIBLE_AMPLITUDE), 'None', sample['injected_pattern']),
        'reviewer': np.random.choice(reviewers, size=len(sample)),
        'review_time': (pd.to_datetime(sample['start_ts']) + pd.to_timedelta(
            np.random.randint(2, 48, size=len(sample)), unit='h')).dt.floor('ms').values,
    })
    print(f'wafer_review_labels rows: {len(labels):,}')
    return labels


# ===============================
# === STDF PTR (Parametric Test Results)
# ===============================

def generate_stdf_ptr_params(prr: pd.DataFrame, target_rows: int = 487_320) -> pd.DataFrame:
    print('Generating stdf_ptr_params...')

    # Select a subset of PRR attempts for parameters (to avoid exploding rows)
    base_parts = prr[['part_id', 'site', 'product', 'wafer_id', 'timestamp', '_r_norm', '_affected', '_severity']].copy()

    # Weight MX-7 parts higher to ensure visibility
    mx7_mask = base_parts['product'] == 'MX-7'
    weights = np.where(mx7_mask, 1.6, 1.0)
    sel_count = int(target_rows / len(PARAMS))  # approx parts to cover
    # Avoid repeated recomputation of weights normalization
    weights = weights.astype(float)
    weights /= weights.sum()
    sel_idx = np.random.choice(base_parts.index.values, size=sel_count, replace=False, p=weights)
    parts_sel = base_parts.loc[sel_idx].copy()

    # Build param rows per selected part
    rows = []
    n_parts = len(parts_sel)
    # Incident dies: AUS MX-7 wafers probed on the re-carded testers (PC-AUS-447 Rev C)
    event_mask_global = parts_sel['_affected'].astype(bool)
    # Contact-resistance shift is strongest at the wafer edge (probe card planarity)
    edge_weight = np.clip((parts_sel['_r_norm'].values - 0.7) / 0.3, 0.0, 1.0)
    # Scale with incident severity so drift eases after the 2025-08-24 rollback
    severity_weight = np.clip(parts_sel['_severity'].values / PEAK_SEVERITY, 0.0, 1.0)
    progress_interval = max(1, len(PARAMS) // 2)

    # Baseline distributions
    # PT_0210 Contact Resistance (mOhm): lognormal, event mean +2.4 sigma (AUS/MX-7)
    CR_mean = 3.40
    CR_sigma = 0.35
    CR_usl = 8.0

    # PT_0217 IDDQ (uA): heavy-tailed (lognormal higher sigma)
    IDDQ_mean = 1.80
    IDDQ_sigma = 0.70
    IDDQ_usl = 6.0

    # PT_0103 Vth margin (mV): log-normal with good Cpk
    VTH_mean = 4.5
    VTH_sigma = 0.30
    VTH_lsl = 3.0

    # PT_0301 extra: mild normal
    EX_mean = 2.2
    EX_sigma = 0.25

    # Generate per parameter
    for j, pcode in enumerate(PARAMS):
        # Event shift mask: incident dies (see event_mask_global)
        event_mask = event_mask_global

        n = n_parts
        if pcode == 'PT_0210':
            base = np.random.lognormal(mean=np.log(CR_mean), sigma=CR_sigma, size=n)
            # shift by up to +2.4 sigma on event, scaled toward the wafer edge
            shift = 2.4 * CR_sigma * (0.4 + 0.6 * edge_weight) * severity_weight
            base[event_mask.values] += shift[event_mask.values]
            value = base
            lsl = np.full(n, np.nan)
            usl = np.full(n, CR_usl)
        elif pcode == 'PT_0217':
            base = np.random.lognormal(mean=np.log(IDDQ_mean), sigma=IDDQ_sigma, size=n)
            # small tails increase during event (correlated with contact issues)
            base[event_mask.values] *= 1.0 + np.random.uniform(0.05, 0.15, size=event_mask.sum()) * severity_weight[event_mask.values]
            value = base
            lsl = np.full(n, np.nan)
            usl = np.full(n, IDDQ_usl)
        elif pcode == 'PT_0103':
            base = np.random.lognormal(mean=np.log(VTH_mean), sigma=VTH_sigma, size=n)
            # slight stability even during event
            value = base
            lsl = np.full(n, VTH_lsl)
            usl = np.full(n, np.nan)
        else:  # PT_0301
            base = np.random.normal(loc=EX_mean, scale=EX_sigma, size=n)
            value = base
            lsl = np.full(n, 1.2)
            usl = np.full(n, 4.0)

        # Introduce tiny nulls in limits (<0.1%)
        null_mask = np.random.rand(n) < 0.001
        if pcode in ['PT_0210', 'PT_0217']:
            usl[null_mask] = np.nan
        else:
            lsl[null_mask] = np.nan

        dfp = pd.DataFrame({
            'part_id': parts_sel['part_id'].values,
            'param_code': pcode,
            'value': value.astype(float),
            'lsl': lsl.astype(float),
            'usl': usl.astype(float),
            'timestamp': pd.to_datetime(parts_sel['timestamp'], errors='coerce').dt.floor('ms'),
            'site': parts_sel['site'].values,
            'product': parts_sel['product'].values,
            'wafer_id': parts_sel['wafer_id'].values,
        })
        rows.append(dfp)

        if (j + 1) % progress_interval == 0 or j == 0:
            progress = ((j + 1) / len(PARAMS)) * 100
            print(f"  Params: {progress:.0f}% ({j + 1:,}/{len(PARAMS):,})")

    ptr = pd.concat(rows, ignore_index=True)

    # Adjust to target rows
    if len(ptr) > target_rows:
        ptr = ptr.sample(n=target_rows, random_state=SEED).reset_index(drop=True)
    elif len(ptr) < target_rows:
        extra = ptr.sample(n=(target_rows - len(ptr)), replace=True, random_state=SEED)
        ptr = pd.concat([ptr, extra], ignore_index=True)

    # Normalize datetime
    ptr['timestamp'] = pd.to_datetime(ptr['timestamp'], errors='coerce').dt.floor('ms')

    print(f'stdf_ptr_params rows: {len(ptr):,}')
    return ptr


# ===============================
# === EQUIPMENT CHANGE LOG
# ===============================

def generate_equip_change_log() -> pd.DataFrame:
    print('Generating equip_change_log...')
    rows = []

    # Regular weekly changes across sites
    change_types = ['probe_card', 'handler_firmware', 'recipe', 'limits', 'program']
    weekly_dates = pd.date_range(RANGE_START, RANGE_END, freq='7D')
    for d in weekly_dates:
        site = np.random.choice(sites, p=[0.5, 0.3, 0.2])
        tester = np.random.choice(TESTERS[site])
        ctype = np.random.choice(change_types, p=[0.18, 0.15, 0.22, 0.25, 0.20])
        scope = np.random.choice(['MX-5', 'MX-7', 'RF-22', 'global'], p=[0.30, 0.35, 0.25, 0.10])
        ver = ''
        if ctype == 'handler_firmware':
            ver = f'HF-{np.random.randint(3,4)}.{np.random.randint(0,4)}.{np.random.randint(0,10)}'
        elif ctype == 'program':
            ver = f'TP-v{np.random.randint(1,3)}.{np.random.randint(0,10)}.{np.random.randint(0,15)}'
        elif ctype == 'probe_card':
            ver = f'Rev {np.random.choice(list("ABC"))}'
        else:
            ver = f'ID-{np.random.randint(100,999)}'
        details = f'{ctype} update {ver}; notes include codes {np.random.choice(["HND-OS-210","PBC-CR-532","TPR-991"]) }'

        change_time = (pd.Timestamp(d).normalize() + pd.Timedelta(hours=np.random.randint(7, 19))).floor('ms')
        rows.append({
            'change_time': change_time,
            'site': site,
            'tester_id': tester,
            'change_type': ctype,
            'scope': scope,
            'ecr_id': f'ECR-{pd.Timestamp(change_time).strftime("%Y%m%d")}-{np.random.randint(100,999)}',
            'details': details,
        })

    # Event: 2025-08-18 07:30 deployment on AUS TST-AUS-03..05 for MX-7
    for tester in ['TST-AUS-03', 'TST-AUS-04', 'TST-AUS-05']:
        rows.append({
            'change_time': EVENT_START,
            'site': 'AUS',
            'tester_id': tester,
            'change_type': 'probe_card',
            'scope': 'MX-7, F12 lots',
            'ecr_id': 'ECR-20250818-447C',
            'details': 'Probe card PC-AUS-447 Rev C installed; contact alarms PBC-CR-532 observed.',
        })
        rows.append({
            'change_time': EVENT_START + pd.Timedelta(minutes=5),
            'site': 'AUS',
            'tester_id': tester,
            'change_type': 'handler_firmware',
            'scope': 'MX-7',
            'ecr_id': 'ECR-20250818-HF321',
            'details': 'Handler firmware HF-3.2.1 deployed; error codes HND-OS-210 logged intermittently.',
        })

    # Recovery: 2025-08-24 22:15 rollback
    for tester in ['TST-AUS-03', 'TST-AUS-04', 'TST-AUS-05']:
        rows.append({
            'change_time': RECOVERY_TIME,
            'site': 'AUS',
            'tester_id': tester,
            'change_type': 'handler_firmware',
            'scope': 'MX-7',
            'ecr_id': 'ECR-20250824-HF319',
            'details': 'Rollback to HF-3.1.9; stabilization begins; alarms clear.',
        })
        rows.append({
            'change_time': RECOVERY_TIME + pd.Timedelta(minutes=10),
            'site': 'AUS',
            'tester_id': tester,
            'change_type': 'probe_card',
            'scope': 'MX-7, F12 lots',
            'ecr_id': 'ECR-20250824-447C-RECOND',
            'details': 'Probe card PC-AUS-447 reconditioned; improved contact resistance.',
        })

    change_df = pd.DataFrame(rows).sort_values('change_time', kind='stable').reset_index(drop=True)
    change_df['change_time'] = pd.to_datetime(change_df['change_time'], errors='coerce').dt.floor('ms')
    print(f'equip_change_log rows: {len(change_df):,}')
    return change_df




# ===============================
# === QUICK QA SUMMARIES
# ===============================

def print_qa_summaries(prr: pd.DataFrame, ptr: pd.DataFrame, change_df: pd.DataFrame, truth: pd.DataFrame):
    print('\nQuality checks:')
    # AUS MX-7 FPY before vs during event
    date = pd.to_datetime(prr['timestamp']).dt.normalize()
    aus_mx7 = (prr['site'] == 'AUS') & (prr['product'] == 'MX-7')
    pre = prr[aus_mx7 & (date < EVENT_START.normalize())]
    during = prr[aus_mx7 & (date >= EVENT_START.normalize()) & (date <= EVENT_END.normalize())]
    post = prr[aus_mx7 & (date > EVENT_END.normalize()) & (date <= pd.Timestamp('2025-09-01'))]
    fpy = lambda df: float((df['pass_flag'] & ~df['retest_flag']).mean()) * 100 if len(df) else np.nan
    print(f"  AUS MX-7 FPY pre-event ~ {fpy(pre):.2f}% | during ~ {fpy(during):.2f}% | post ~ {fpy(post):.2f}%")
    print(f"  All-site FPY ~ {fpy(prr):.2f}%")

    # HB_021 surge
    hb_pre = pre['hard_bin'].value_counts(normalize=True).get('HB_021', 0.0)
    hb_dur = during['hard_bin'].value_counts(normalize=True).get('HB_021', 0.0)
    print(f"  HB_021 share pre {hb_pre*100:.2f}% -> during {hb_dur*100:.2f}% (expected surge)")

    # Retest rate
    print(f"  Retest rate pre ~ {pre['retest_flag'].mean()*100:.2f}% -> during ~ {during['retest_flag'].mean()*100:.2f}%")

    # Param PT_0210 mean shift at AUS/MX-7 during event
    cr = ptr[(ptr['site'] == 'AUS') & (ptr['product'] == 'MX-7') & (ptr['param_code'] == 'PT_0210')]
    pdate = pd.to_datetime(cr['timestamp']).dt.normalize()
    m_pre = cr[pdate < EVENT_START.normalize()]['value'].mean()
    m_dur = cr[(pdate >= EVENT_START.normalize()) & (pdate <= EVENT_END.normalize())]['value'].mean()
    print(f"  PT_0210 mean pre {m_pre:.2f} -> during {m_dur:.2f} (contact resistance shift)")

    # Change log key entries
    onset = change_df[(change_df['change_time'] == EVENT_START) & (change_df['site'] == 'AUS')]
    rollback = change_df[(change_df['change_time'] == RECOVERY_TIME) & (change_df['site'] == 'AUS')]
    print(f"  Change log onset entries: {len(onset):,} | rollback entries: {len(rollback):,}")

    # Wafer patterns
    print(f"  Wafers: {len(truth):,} | incident wafers: {int(truth['affected'].sum()):,}")
    print('  Injected pattern mix: ' + ', '.join(
        f'{k}={v}' for k, v in truth['injected_pattern'].value_counts().items()))


# ===============================
# === MAIN
# ===============================
if __name__ == '__main__':
    args = parse_args()
    if not args.local:
        os.environ['CATALOG'] = args.catalog
        os.environ['SCHEMA'] = args.schema
        os.environ['VOLUME'] = args.volume

    print('Starting STDF wafer-sort data generation...')
    print('-' * 60)

    lot_master, prr, truth = generate_wafer_sort()
    ptr = generate_stdf_ptr_params(prr, target_rows=487_320)
    change_log = generate_equip_change_log()
    labels = generate_wafer_review_labels(truth)

    print_qa_summaries(prr, ptr, change_log, truth)

    prr = prr.drop(columns=['_r_norm', '_affected', '_severity'])
    save_to_parquet(lot_master, 'stdf_raw_lot_wafer_master', num_files=2)
    save_to_parquet(prr, 'stdf_raw_prr_parts', num_files=8)
    save_to_parquet(ptr, 'stdf_raw_ptr_params', num_files=10)
    save_to_parquet(change_log, 'stdf_raw_equip_change_log', num_files=1)
    save_to_parquet(labels, 'stdf_raw_wafer_review_labels', num_files=1)

    print('\n' + '=' * 60)
    print('GENERATION COMPLETE - Raw STDF datasets saved. All timestamps are naive, floored to ms.')
    print('=' * 60)
