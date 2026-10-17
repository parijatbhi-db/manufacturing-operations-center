import { useEffect, useRef, useState } from 'react';
import { PipelineStatus, pipelineRun, pipelineStatus } from '../lib/api';
import { IconExternal, IconRefresh } from './Icons';

// What each job task does, for the business audience
const TASK_LABELS: Record<string, string> = {
  generate_data: 'Generate STDF wafer-sort files',
  ingest_raw: 'Lakeflow ingest (Auto Loader + expectations)',
  sql_transformations: 'Silver / gold + wafer-map features',
  wafer_ml: 'Train + score wafer anomaly and pattern models',
  wafer_patterns_gold: 'Wafer-pattern gold tables from model output',
  refresh_lakebase: 'Refresh Lakebase serving tables',
  export_kb_docs: 'Regenerate Knowledge Assistant docs',
  sync_agent_bricks: 'Sync Knowledge Assistant + Supervisor Agent',
};

function fmt(ts: number | null) {
  return ts ? new Date(ts).toLocaleString() : '—';
}

function duration(start: number, end: number | null) {
  const s = Math.round(((end || Date.now()) - start) / 1000);
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

// Anything not in a terminal state (incl. TERMINATING, WAITING on a retry) is still in progress.
const TERMINAL_STATES = ['TERMINATED', 'SKIPPED', 'INTERNAL_ERROR'];

function stateClass(state: string, result: string | null) {
  if (result === 'SUCCESS') return 'ok';
  if (state && !TERMINAL_STATES.includes(state)) return 'running';
  if (result) return 'bad';
  return '';
}

export default function PipelineView() {
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  // Run started from this tab that runs/list hasn't returned yet
  const [pendingRunId, setPendingRunId] = useState<number | null>(null);
  const timer = useRef<number | null>(null);

  async function load() {
    try {
      setStatus(await pipelineStatus());
      setError(null);
    } catch (e: any) {
      setError(e.message);
    }
  }

  const pendingListed = pendingRunId != null && !!status?.runs.some((r) => r.runId === pendingRunId);
  useEffect(() => { if (pendingListed) setPendingRunId(null); }, [pendingListed]);

  const running = pendingRunId != null || !!status?.runs.some((r) => stateClass(r.state, r.result) === 'running');

  useEffect(() => { load(); }, []);
  // Poll while a run is active or a just-started run hasn't been listed yet
  useEffect(() => {
    if (timer.current) window.clearInterval(timer.current);
    if (running) timer.current = window.setInterval(load, pendingRunId != null ? 3000 : 15000);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [running, pendingRunId]);

  async function refresh() {
    setStarting(true);
    setError(null);
    try {
      const { runId } = await pipelineRun();
      setPendingRunId(runId);
      // Safety valve so a run that never shows up in runs/list can't keep the tab polling forever
      window.setTimeout(() => setPendingRunId((id) => (id === runId ? null : id)), 120_000);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setStarting(false);
    }
  }

  const last = status?.runs[0];

  return (
    <>
      <div className="view-header">
        <div>
          <div className="view-title">Data Pipeline</div>
          <div className="view-subtitle">Lakeflow Job that refreshes every asset in this app, end to end</div>
        </div>
        <div className="view-actions">
          {status && <a className="btn" href={status.job.url} target="_blank" rel="noreferrer"><IconExternal /> Open job</a>}
          <button className="btn" onClick={load}><IconRefresh /> Reload</button>
          <button className="btn primary" disabled={starting || running} onClick={refresh}>
            {running ? 'Refresh running…' : starting ? 'Starting…' : 'Refresh data'}
          </button>
        </div>
      </div>
      <div className="view-body pipeline-body">
        {error && <div className="ops-error">{error}</div>}
        {status && (
          <>
            <div className="pipe-card">
              <div className="disp-title">{status.job.name}</div>
              <div className="pipe-steps">
                {status.job.tasks.map((t, i) => {
                  const lastTask = last?.tasks.find((x) => x.key === t.key);
                  return (
                    <div key={t.key} className={`pipe-step ${lastTask ? stateClass(lastTask.state, lastTask.result) : ''}`}>
                      <div className="pipe-step-n">{i + 1}</div>
                      <div>
                        <div className="pipe-step-name">{TASK_LABELS[t.key] || t.key}</div>
                        <div className="pipe-step-key">{t.key}{lastTask ? ` · ${lastTask.result || lastTask.state}` : ''}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="pipe-card">
              <div className="disp-title">Recent runs</div>
              <table className="pipe-runs">
                <thead>
                  <tr><th>Started</th><th>Duration</th><th>Status</th><th /></tr>
                </thead>
                <tbody>
                  {status.runs.map((r) => (
                    <tr key={r.runId}>
                      <td>{fmt(r.startTime)}</td>
                      <td>{duration(r.startTime, r.endTime)}</td>
                      <td><span className={`run-pill ${stateClass(r.state, r.result)}`}>{r.result || r.state}</span></td>
                      <td><a href={r.url} target="_blank" rel="noreferrer">Run {r.runId} <IconExternal /></a></td>
                    </tr>
                  ))}
                  {!status.runs.length && <tr><td colSpan={4}>No runs yet.</td></tr>}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </>
  );
}
