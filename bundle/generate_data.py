# generate_data.py for kari_semi_stdf demo
# RAW synthetic datasets that follow demo_story.json
# - stdf_prr_parts
# - stdf_ptr_params
# - equip_change_log
# - lot_wafer_master
#
# Contracts:
# - Schema fidelity (columns/dtypes)
# - Referential integrity between PRR and PTR (part_id, wafer_id, lot_id)
# - Event visibility: AUS MX-7 FPY drop 2025-08-18..2025-08-27 with HB_021/PT_0210 shifts, retest spike
# - Seasonality: weekday/weekend volume, business-hour timestamps
# - Datetime naive, floored to ms

import random
from datetime import timedelta

import numpy as np
import pandas as pd
from faker import Faker

from utils import save_to_parquet

# Set environment variables for Databricks Volumes
import os
os.environ['CATALOG'] = 'parijat_demos'
os.environ['SCHEMA'] = 'manufacturing'
os.environ['VOLUME'] = 'raw_data'



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

# ===============================
# === LOT & WAFER MASTER
# ===============================

def generate_lot_wafer_master() -> pd.DataFrame:
    print('Generating lot_wafer_master...')
    rows = []

    # Define MX-7 focus lots in F12 with wafers W27..W29, 12-14 wafers each
    mx7_focus = []
    for wlot in ['W27', 'W28', 'W29']:
        lot_id = f'MX7-F12-{wlot}'
        n_wafers = np.random.randint(12, 15)
        for wi in range(1, n_wafers + 1):
            wafer_id = f'{lot_id}-W{wi:02d}'
            dies = int(np.random.normal(980, 40))  # 900-1100 typical
            start_date = (pd.Timestamp('2025-08-10') + pd.Timedelta(days=np.random.randint(-5, 5))).normalize()
            rows.append({
                'lot_id': lot_id,
                'wafer_id': wafer_id,
                'product': 'MX-7',
                'foundry': 'F12',
                'dies_per_wafer_expected': int(np.clip(dies, 860, 1150)),
                'start_date': start_date.floor('ms'),
                'site_planned': 'AUS',
            })
            mx7_focus.append(wafer_id)

    # Other lots across products and foundries
    def gen_random_lots(prod: str, fnd: str, count_lots: int, site_hint: str):
        for i in range(count_lots):
            lot_id = f"{prod.replace('-', '')}-{fnd}-L{i+1:03d}"
            n_wafers = np.random.randint(8, 16)
            for wi in range(1, n_wafers + 1):
                wafer_id = f'{lot_id}-W{wi:02d}'
                dies = int(np.random.normal(960 if prod == 'MX-5' else 1020, 50))
                start_date = (pd.Timestamp('2025-06-15') + pd.Timedelta(days=np.random.randint(0, 60))).normalize()
                rows.append({
                    'lot_id': lot_id,
                    'wafer_id': wafer_id,
                    'product': prod,
                    'foundry': fnd,
                    'dies_per_wafer_expected': int(np.clip(dies, 820, 1200)),
                    'start_date': start_date.floor('ms'),
                    'site_planned': site_hint,
                })

    gen_random_lots('MX-7', 'F10', 9, 'HSC')
    gen_random_lots('MX-5', 'F10', 16, 'AUS')
    gen_random_lots('MX-5', 'F12', 10, 'HSC')
    gen_random_lots('RF-22', 'F10', 12, 'PNG')
    gen_random_lots('RF-22', 'F12', 8, 'PNG')

    lot_df = pd.DataFrame(rows)
    lot_df['start_date'] = pd.to_datetime(lot_df['start_date'], errors='coerce').dt.floor('ms')
    print(f'lot_wafer_master rows: {len(lot_df):,}')
    return lot_df

# ===============================
# === UTIL HELPERS
# ===============================

def _choose(values, probs, size):
    p = np.array(probs, dtype=float)
    p = p / p.sum()
    return np.random.choice(values, size=size, p=p)


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


# ===============================
# === STDF PRR (Part Results)
# ===============================

def generate_stdf_prr_parts(lot_df: pd.DataFrame, target_rows: int = 326_450) -> pd.DataFrame:
    print('Generating stdf_prr_parts...')
    rows = []

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
    # Normalize to 1
    total_share = sum(site_product_share.values())
    for k in site_product_share:
        site_product_share[k] = site_product_share[k] / total_share

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

    # Retest baseline
    RETEST_BASE = {s: 0.025 for s in sites}

    # Hard bin mixes (baseline fail share for non-pass)
    FAIL_MIX_BASE = np.array([0.40, 0.35, 0.15, 0.10])  # HB_014, HB_021, HB_007, HB_032

    # Prepare wafer assignments per site/product using lot master
    lot_df = lot_df.copy()
    # Map wafers by product/foundry/site_planned
    wafers_by_key = {}
    for site in sites:
        for prod in products:
            keys = lot_df[(lot_df['product'] == prod) & (lot_df['site_planned'] == site)]['wafer_id'].values
            wafers_by_key[(site, prod)] = keys

    # Precompute wafer lookups and business hour probabilities
    wafer_to_lot = lot_df.set_index('wafer_id')['lot_id']
    wafer_to_foundry = lot_df.set_index('wafer_id')['foundry']
    weekday_hp = business_hour_probs(True)
    weekend_hp = business_hour_probs(False)

    # Part_id X/Y grid helper
    def make_part_id(prod: str, wafer_id: str, xi: int, yi: int, attempt: int = 1) -> str:
        prefix = prod.replace('-', '')
        base = f"{prefix}-{wafer_id}-X{xi:03d}Y{yi:03d}"
        return base if attempt == 1 else f"{base}-R{attempt}"

    # Iterate days with progress
    total_days = len(DAYS)
    progress_interval = max(1, total_days // 10)

    seq_counter = 0

    day_values = DAYS.values
    for i, d in enumerate(day_values):
        if (i + 1) % progress_interval == 0 or i == 0:
            progress = ((i + 1) / total_days) * 100
            print(f"  Days: {progress:.0f}% ({i + 1:,}/{total_days:,})")

        day = pd.Timestamp(d).normalize()
        dow_mul = DOW_MUL.get(day.weekday(), 1.0)

        # Base volume per day
        base_daily = int(np.random.normal(3100, 350))
        base_daily = int(max(1200, base_daily))
        vol_today = int(base_daily * dow_mul)

        # Split by site/product
        combos = list(site_product_share.keys())
        shares = np.array([site_product_share[c] for c in combos])
        shares = shares / shares.sum()
        counts = np.random.multinomial(vol_today, shares)

        # Event effects for AUS/MX-7 on event window
        in_event = (day >= EVENT_START.normalize()) and (day <= EVENT_END.normalize())
        event_intensity = 1.0
        if in_event:
            # Increase retest and lower FPY
            event_intensity = 1.0

        # For each combo, generate parts
        for (site, prod), cnt in zip(combos, counts):
            if cnt <= 0:
                continue
            # Choose wafers available for site/product, fallback global product wafers
            wafers = wafers_by_key.get((site, prod))
            if wafers is None or len(wafers) == 0:
                wafers = lot_df[lot_df['product'] == prod]['wafer_id'].values
            wafers_pick = np.random.choice(wafers, size=cnt, replace=True)

            tester_pool = TESTERS[site]
            testers_pick = np.random.choice(tester_pool, size=cnt, replace=True)

            # Timestamp per part within business hours
            is_weekday = day.weekday() < 5
            hp = weekday_hp if is_weekday else weekend_hp
            hours = np.random.choice(HOURS, size=cnt, p=hp)
            minutes = np.random.randint(0, 60, size=cnt)
            total_minutes = hours * 60 + minutes
            ts = (pd.Timestamp(day) + pd.to_timedelta(total_minutes, unit='m')).floor('ms')

            # Program versions by product
            program_vers = np.random.choice([
                'TP-v1.8.3', 'TP-v1.8.4', 'TP-v1.9.0', 'TP-v2.0.1'
            ], size=cnt, p=[0.35, 0.30, 0.25, 0.10])

            # Probe card / handler assignment
            pc_pool = PROBE_CARDS[site]
            hd_pool = HANDLERS[site]
            probe_card_ids = np.random.choice(pc_pool, size=cnt, replace=True)
            handler_ids = np.random.choice(hd_pool, size=cnt, replace=True)

            # FPY and retest rates with event effect applied for AUS/MX-7
            fpy = FPY_BASE.get((site, prod), 0.95)
            retest_rate = RETEST_BASE[site]

            # Inject AUS/MX-7 anomaly window behavior
            if site == 'AUS' and prod == 'MX-7':
                # Baseline FPY ~95%; drop to 88.1-89.0 during event, partial recovery 93% by 08-27
                if in_event:
                    # For days after rollback (>= RECOVERY_TIME date), partially recover
                    if day >= RECOVERY_TIME.normalize():
                        # linear recovery towards 0.93 by 08-27
                        days_to_end = (EVENT_END.normalize() - RECOVERY_TIME.normalize()).days
                        days_into_recovery = (day - RECOVERY_TIME.normalize()).days
                        recovery_frac = np.clip(days_into_recovery / max(1, days_to_end), 0.0, 1.0)
                        target_fpy_day = 0.89 + 0.04 * recovery_frac  # 0.89 up to ~0.93
                        fpy = target_fpy_day
                        retest_rate = 0.07 + 0.02 * (1.0 - recovery_frac)  # 7-9% then easing
                    else:
                        # Within 08-18..08-23 -> 88.1..89.0
                        fpy = np.random.uniform(0.881, 0.890)
                        retest_rate = np.random.uniform(0.07, 0.09)
                elif day > EVENT_END.normalize() and day <= pd.Timestamp('2025-09-01'):
                    # normalize back to ~95% by 09-01
                    days_after = (day - EVENT_END.normalize()).days
                    fpy = min(0.95, 0.93 + 0.02 * (days_after / max(1, (pd.Timestamp('2025-09-01') - EVENT_END.normalize()).days)))
                    retest_rate = 0.04
                else:
                    # baseline mild variation
                    fpy = np.random.normal(FPY_BASE[(site, prod)], 0.002)
                    retest_rate = RETEST_BASE[site]

            else:
                # Non-incident combos: mild noise
                fpy = np.random.normal(fpy, 0.002)
                retest_rate = np.random.normal(retest_rate, 0.003)
                retest_rate = float(np.clip(retest_rate, 0.0, 0.06))

            pass_first = np.random.rand(cnt) < fpy
            retest_flag = np.random.rand(cnt) < retest_rate

            # Hard/soft bin assignment: pass bins vs failure bins
            hard_bin = np.empty(cnt, dtype=object)
            soft_bin = np.empty(cnt, dtype=object)

            # Default baseline failure mix
            mix = FAIL_MIX_BASE.copy()
            # During event window at AUS/MX-7, amplify HB_021 by ~3.2x relative share
            if site == 'AUS' and prod == 'MX-7' and in_event:
                # reweight mix: bump HB_021
                mix = FAIL_MIX_BASE.copy()
                mix[1] = mix[1] * 3.2
                mix = mix / mix.sum()

            fail_choice = np.random.choice(FAIL_BINS, size=cnt, p=mix)
            soft_fail_choice = np.random.choice(SOFT_FAILS, size=cnt, p=mix)

            hard_bin[pass_first] = np.random.choice([HARD_PASS_PRIMARY, HARD_PASS_SECOND], size=pass_first.sum(), p=[0.85, 0.15])
            soft_bin[pass_first] = SOFT_PASS
            hard_bin[~pass_first] = fail_choice[~pass_first]
            soft_bin[~pass_first] = soft_fail_choice[~pass_first]

            # Part_id components: X/Y index
            xi = np.random.randint(0, 300, size=cnt)
            yi = np.random.randint(0, 300, size=cnt)

            # Attempt number: introduce a small fraction with retest attempts (>1)
            attempt_num = np.ones(cnt, dtype=int)
            retry_mask = (retest_flag) & (~pass_first)
            if retry_mask.any():
                attempt_num[retry_mask] = 2

            # Vectorized part_id construction for speed while preserving exact formatting
            prefix = prod.replace('-', '')
            # Precompute constant strings for vectorized concatenation
            w_ids = wafers_pick.astype(str)
            x_str = np.char.add('X', np.char.zfill(xi.astype(str), 3))
            y_str = np.char.add('Y', np.char.zfill(yi.astype(str), 3))
            base = np.char.add(np.char.add(np.char.add(prefix + '-', w_ids), '-'), np.char.add(x_str, y_str))
            # attempt suffix only when attempt > 1
            attempt_suffix = np.where(attempt_num == 1, '', np.char.add('-R', attempt_num.astype(str)))
            part_ids = np.char.add(base, attempt_suffix)
            part_ids = part_ids.astype(object)

            lot_ids = wafer_to_lot.loc[wafers_pick].values
            foundry = wafer_to_foundry.loc[wafers_pick].values

            df_chunk = pd.DataFrame({
                'part_id': part_ids,
                'site': site,
                'tester_id': testers_pick,
                'wafer_id': wafers_pick,
                'lot_id': lot_ids,
                'foundry': foundry,
                'product': prod,
                'timestamp': ts,
                'soft_bin': soft_bin,
                'hard_bin': hard_bin,
                'pass_flag': pass_first.astype(bool),
                'retest_flag': retest_flag.astype(bool),
                'program_version': program_vers,
                'probe_card_id': probe_card_ids,
                'handler_id': handler_ids,
            })
            rows.append(df_chunk)

    prr = pd.concat(rows, ignore_index=True)

    # Final normalization of datetime (no tz ops to avoid overhead; timestamps are naive already)
    prr['timestamp'] = pd.to_datetime(prr['timestamp'], errors='coerce').dt.floor('ms')

    # Shuffle and down/up sample to target row count (approx)
    prr = prr.sample(frac=1.0, random_state=SEED).reset_index(drop=True)
    if len(prr) > target_rows:
        prr = prr.iloc[:target_rows].copy()
    elif len(prr) < target_rows:
        # pad by sampling additional rows from existing days (safe for raw)
        extra = prr.sample(n=(target_rows - len(prr)), replace=True, random_state=SEED)
        prr = pd.concat([prr, extra], ignore_index=True)

    print(f'stdf_prr_parts rows: {len(prr):,}')
    return prr


# ===============================
# === STDF PTR (Parametric Test Results)
# ===============================

def generate_stdf_ptr_params(prr: pd.DataFrame, target_rows: int = 487_320) -> pd.DataFrame:
    print('Generating stdf_ptr_params...')

    # Select a subset of PRR attempts for parameters (to avoid exploding rows)
    base_parts = prr[['part_id', 'site', 'product', 'wafer_id', 'timestamp']].copy()

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
    ts = pd.to_datetime(parts_sel['timestamp'], errors='coerce').dt.floor('ms')
    # Ensure tz-naive alignment before comparisons
    ts = ts.dt.tz_localize(None)
    ts_norm = ts.dt.normalize()
    in_event_mask = (ts_norm >= EVENT_START.normalize()) & (ts_norm <= EVENT_END.normalize())
    aus_mx7_mask = (parts_sel['site'] == 'AUS') & (parts_sel['product'] == 'MX-7')
    event_mask_global = in_event_mask & aus_mx7_mask
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
        vals = []
        # Event shift mask: AUS + MX-7 + in event window
        # use precomputed event_mask_global for reproducibility and performance
        event_mask = event_mask_global

        n = n_parts
        if pcode == 'PT_0210':
            base = np.random.lognormal(mean=np.log(CR_mean), sigma=CR_sigma, size=n)
            # shift by +2.4 sigma on event
            shift = 2.4 * CR_sigma
            base[event_mask.values] += shift
            value = base
            lsl = np.full(n, np.nan)
            usl = np.full(n, CR_usl)
        elif pcode == 'PT_0217':
            base = np.random.lognormal(mean=np.log(IDDQ_mean), sigma=IDDQ_sigma, size=n)
            # small tails increase during event (correlated with contact issues)
            base[event_mask.values] *= np.random.uniform(1.05, 1.15, size=event_mask.sum())
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

def print_qa_summaries(prr: pd.DataFrame, ptr: pd.DataFrame, change_df: pd.DataFrame):
    print('\nQuality checks:')
    # AUS MX-7 FPY before vs during event
    prr['date'] = pd.to_datetime(prr['timestamp'], errors='coerce').dt.tz_localize(None).dt.floor('ms').dt.normalize()
    aus_mx7 = prr[(prr['site'] == 'AUS') & (prr['product'] == 'MX-7')]
    pre = aus_mx7[aus_mx7['date'] < EVENT_START.normalize()]
    during = aus_mx7[(aus_mx7['date'] >= EVENT_START.normalize()) & (aus_mx7['date'] <= EVENT_END.normalize())]
    post = aus_mx7[(aus_mx7['date'] > EVENT_END.normalize()) & (aus_mx7['date'] <= pd.Timestamp('2025-09-01'))]
    fpy_pre = float(pre['pass_flag'].mean()) if len(pre) else np.nan
    fpy_dur = float(during['pass_flag'].mean()) if len(during) else np.nan
    fpy_post = float(post['pass_flag'].mean()) if len(post) else np.nan
    print(f"  AUS MX-7 FPY pre-event ~ {fpy_pre*100:.2f}% | during ~ {fpy_dur*100:.2f}% | post ~ {fpy_post*100:.2f}%")

    # HB_021 surge
    hb_pre = pre['hard_bin'].value_counts(normalize=True).get('HB_021', 0.0)
    hb_dur = during['hard_bin'].value_counts(normalize=True).get('HB_021', 0.0)
    print(f"  HB_021 share pre {hb_pre*100:.2f}% -> during {hb_dur*100:.2f}% (expected surge)")

    # Retest rate
    rr_pre = float(pre['retest_flag'].mean()) if len(pre) else np.nan
    rr_dur = float(during['retest_flag'].mean()) if len(during) else np.nan
    print(f"  Retest rate pre ~ {rr_pre*100:.2f}% -> during ~ {rr_dur*100:.2f}%")

    # Param PT_0210 mean shift at AUS/MX-7 during event
    ptr['date'] = pd.to_datetime(ptr['timestamp'], errors='coerce').dt.tz_localize(None).dt.floor('ms').dt.normalize()
    m_pre = ptr[(ptr['site'] == 'AUS') & (ptr['product'] == 'MX-7') & (ptr['param_code'] == 'PT_0210') & (ptr['date'] < EVENT_START.normalize())]['value'].mean()
    m_dur = ptr[(ptr['site'] == 'AUS') & (ptr['product'] == 'MX-7') & (ptr['param_code'] == 'PT_0210') & (ptr['date'] >= EVENT_START.normalize()) & (ptr['date'] <= EVENT_END.normalize())]['value'].mean()
    print(f"  PT_0210 mean pre {m_pre:.2f} -> during {m_dur:.2f} (contact resistance shift)")

    # Change log key entries
    onset = change_df[(change_df['change_time'] == EVENT_START) & (change_df['site'] == 'AUS')]
    rollback = change_df[(change_df['change_time'] == RECOVERY_TIME) & (change_df['site'] == 'AUS')]
    print(f"  Change log onset entries: {len(onset):,} | rollback entries: {len(rollback):,}")


# ===============================
# === MAIN
# ===============================
if __name__ == '__main__':
    print('Starting KARI Semiconductor STDF data generation...')
    print('-' * 60)

    lot_master = generate_lot_wafer_master()
    save_to_parquet(lot_master, 'stdf_raw_lot_wafer_master', num_files=2)

    prr = generate_stdf_prr_parts(lot_master, target_rows=326_450)
    save_to_parquet(prr, 'stdf_raw_prr_parts', num_files=8)

    ptr = generate_stdf_ptr_params(prr, target_rows=487_320)
    save_to_parquet(ptr, 'stdf_raw_ptr_params', num_files=10)

    change_log = generate_equip_change_log()
    save_to_parquet(change_log, 'stdf_raw_equip_change_log', num_files=1)

    print_qa_summaries(prr, ptr, change_log)

    print('\n' + '=' * 60)
    print('GENERATION COMPLETE - Raw STDF datasets saved. All timestamps are naive, floored to ms.')
    print('=' * 60)
