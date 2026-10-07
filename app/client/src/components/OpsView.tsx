import { useEffect, useMemo, useState } from 'react';
import { OpsWafer, OpsWaferDetail, opsGetWafer, opsListWafers, opsSetDisposition } from '../lib/api';
import { IconRefresh } from './Icons';

const PATTERNS = ['Edge-Ring', 'Edge-Loc', 'Center', 'Donut', 'Loc', 'Scratch', 'Near-full'];
const SITES = ['AUS', 'HSC', 'PNG'];
const DISPOSITIONS = [
  { key: 'HOLD', label: 'Hold', hint: 'Quarantine wafer pending review' },
  { key: 'REPROBE', label: 'Re-probe', hint: 'Send back to sort' },
  { key: 'RELEASE', label: 'Release', hint: 'Ship as tested' },
  { key: 'SCRAP', label: 'Scrap', hint: 'Remove from flow' },
];

// Die colors by bin (pass vs failure mechanism)
const BIN_COLORS: Record<string, string> = {
  Pass: '#CFE3D8',
  HB_021: '#FF3621', // open/short contact
  HB_014: '#F2A93B', // IDDQ leakage
  HB_007: '#7B5EA7', // functional
  HB_032: '#2F7FB5', // parametric
};

function pct(v: number | null | undefined) {
  return v == null ? '—' : `${(v * 100).toFixed(1)}%`;
}

function WaferMap({ dies }: { dies: OpsWaferDetail['dies'] }) {
  const size = useMemo(() => Math.max(1, ...dies.map((d) => Math.max(d.die_x, d.die_y))) + 1, [dies]);
  const cell = 300 / size;
  return (
    <svg viewBox="0 0 300 300" className="wafer-map" role="img" aria-label="Wafer die map">
      <circle cx="150" cy="150" r="149" className="wafer-outline" />
      {dies.map((d) => (
        <rect
          key={`${d.die_x}-${d.die_y}`}
          x={d.die_x * cell + 0.5}
          y={d.die_y * cell + 0.5}
          width={cell - 1}
          height={cell - 1}
          fill={BIN_COLORS[d.bin_label] || '#999'}
        >
          <title>{`X${d.die_x} Y${d.die_y}: ${d.bin_label}`}</title>
        </rect>
      ))}
    </svg>
  );
}

export default function OpsView() {
  const [pattern, setPattern] = useState('');
  const [site, setSite] = useState('');
  const [status, setStatus] = useState('open');
  const [wafers, setWafers] = useState<OpsWafer[]>([]);
  const [listLatency, setListLatency] = useState<number | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<OpsWaferDetail | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadList() {
    setError(null);
    try {
      const r = await opsListWafers({ pattern, site, status });
      setWafers(r.wafers);
      setListLatency(r.latencyMs);
      if (!selected && r.wafers.length) setSelected(r.wafers[0].wafer_id);
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function loadDetail(id: string) {
    try {
      setDetail(await opsGetWafer(id));
    } catch (e: any) {
      setError(e.message);
    }
  }

  useEffect(() => { loadList(); }, [pattern, site, status]);
  useEffect(() => { if (selected) loadDetail(selected); }, [selected]);

  async function disposition(key: string) {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await opsSetDisposition(selected, key, note);
      setNote('');
      await Promise.all([loadDetail(selected), loadList()]);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const w = detail?.wafer;
  const binCounts = useMemo(() => {
    const c: Record<string, number> = {};
    detail?.dies.forEach((d) => { if (d.bin_label !== 'Pass') c[d.bin_label] = (c[d.bin_label] || 0) + 1; });
    return Object.entries(c).sort((a, b) => b[1] - a[1]);
  }, [detail]);

  return (
    <>
      <div className="view-header">
        <div>
          <div className="view-title">Wafer Operations</div>
          <div className="view-subtitle">
            Flagged wafers served from Lakebase · dispositions written back to Postgres
            {listLatency != null && <span className="latency-chip">queue in {listLatency} ms</span>}
          </div>
        </div>
        <div className="view-actions">
          <button className="btn" onClick={loadList}><IconRefresh /> Reload</button>
        </div>
      </div>
      <div className="view-body ops-body">
        <section className="ops-queue">
          <div className="ops-filters">
            <select value={pattern} onChange={(e) => setPattern(e.target.value)}>
              <option value="">All patterns</option>
              {PATTERNS.map((p) => <option key={p}>{p}</option>)}
            </select>
            <select value={site} onChange={(e) => setSite(e.target.value)}>
              <option value="">All sites</option>
              {SITES.map((s) => <option key={s}>{s}</option>)}
            </select>
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="open">Open (no disposition / on hold)</option>
              <option value="all">All flagged</option>
            </select>
          </div>
          <div className="ops-count">{wafers.length} wafers</div>
          <div className="ops-list">
            {wafers.map((x) => (
              <button
                key={x.wafer_id}
                className={`ops-row ${selected === x.wafer_id ? 'active' : ''}`}
                onClick={() => setSelected(x.wafer_id)}
              >
                <div className="ops-row-top">
                  <span className="ops-wafer">{x.wafer_id}</span>
                  <span className={`pattern-tag p-${x.pattern_class}`}>{x.pattern_class}</span>
                </div>
                <div className="ops-row-meta">
                  {x.sort_date} · {x.site} · {x.tester_id} · {x.probe_card_id} · yield {pct(x.wafer_yield)}
                  {x.is_anomalous && <> · <span className="anomaly-flag">anomaly {x.anomaly_score.toFixed(2)}</span></>}
                </div>
                {x.disposition && <div className={`disp-tag d-${x.disposition}`}>{x.disposition}</div>}
              </button>
            ))}
            {!wafers.length && !error && <div className="ops-empty">No wafers match these filters.</div>}
          </div>
        </section>

        <section className="ops-detail">
          {error && <div className="ops-error">{error}</div>}
          {!w && !error && <div className="ops-empty">Select a wafer from the queue.</div>}
          {w && detail && (
            <>
              <div className="ops-detail-head">
                <div>
                  <div className="ops-detail-title">{w.wafer_id}</div>
                  <div className="ops-detail-sub">
                    Lot {w.lot_id} · {w.product} ({w.foundry}) · sorted {w.sort_date} on {w.tester_id} / {w.probe_card_id} / {w.handler_id}
                  </div>
                </div>
                <span className="latency-chip">loaded from Lakebase in {detail.latencyMs} ms</span>
              </div>

              <div className="ops-detail-grid">
                <div className="ops-map-card">
                  <WaferMap dies={detail.dies} />
                  <div className="map-legend">
                    {Object.entries(BIN_COLORS).map(([k, c]) => (
                      <span key={k}><i style={{ background: c }} />{k}</span>
                    ))}
                  </div>
                </div>
                <div className="ops-facts">
                  <div className="fact"><span>Pattern (ML)</span><b><span className={`pattern-tag p-${w.pattern_class}`}>{w.pattern_class}</span> {pct(w.pattern_confidence)} confidence</b></div>
                  <div className="fact"><span>Anomaly score</span><b>{w.anomaly_score.toFixed(3)} {w.is_anomalous ? '(anomalous map)' : '(within normal range)'}</b></div>
                  <div className="fact"><span>Likely cause</span><b>{w.likely_cause}</b></div>
                  <div className="fact"><span>Engineer label</span><b>{w.reviewed_pattern || 'Not reviewed'}</b></div>
                  <div className="fact"><span>Rule baseline</span><b>{w.rule_pattern_class}</b></div>
                  <div className="fact"><span>Wafer yield</span><b>{pct(w.wafer_yield)} ({w.dies_pass}/{w.dies_tested} dies)</b></div>
                  <div className="fact"><span>Edge vs center fail</span><b>{pct(w.edge_fail_rate)} vs {pct(w.center_fail_rate)}</b></div>
                  <div className="fact"><span>Failing bins</span><b>{binCounts.map(([b, n]) => `${b} ${n}`).join(' · ') || '—'}</b></div>

                  <div className="disp-box">
                    <div className="disp-title">Record disposition</div>
                    <textarea
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      placeholder="Note (e.g. probe card PC-AUS-447 pulled for planarity check)"
                      rows={2}
                    />
                    <div className="disp-buttons">
                      {DISPOSITIONS.map((d) => (
                        <button key={d.key} className={`btn disp-btn d-${d.key}`} disabled={busy} title={d.hint} onClick={() => disposition(d.key)}>
                          {d.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="disp-history">
                    <div className="disp-title">History</div>
                    {detail.history.length === 0 && <div className="ops-empty small">No dispositions yet.</div>}
                    {detail.history.map((h, i) => (
                      <div key={i} className="hist-row">
                        <span className={`disp-tag d-${h.disposition}`}>{h.disposition}</span>
                        <span className="hist-meta">{new Date(h.entered_at).toLocaleString()} · {h.entered_by}</span>
                        {h.note && <div className="hist-note">{h.note}</div>}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </>
          )}
        </section>
      </div>
    </>
  );
}
