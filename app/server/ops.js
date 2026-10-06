// Wafer Operations (Lakebase) and Data Pipeline (Lakeflow Job) routes.

import { ensureSchema, getServicePrincipalToken, lakebaseConfigured, query } from './lakebase.js';

const SYNCED_SCHEMA = process.env.LAKEBASE_SYNCED_SCHEMA || 'mfg_ops';
const REFRESH_JOB_ID = process.env.REFRESH_JOB_ID;
const DISPOSITIONS = ['HOLD', 'REPROBE', 'RELEASE', 'SCRAP'];

function workspaceHost() {
  let host = process.env.DATABRICKS_HOST || '';
  if (host && !host.startsWith('http')) host = `https://${host}`;
  return host;
}

function sendError(res, err) {
  console.error('[ops error]', err.status || 500, err.message);
  res.status(err.status || 500).json({ error: err.message });
}

// Synced table names come from the bundle (lb_*); schema is a fixed identifier, not user input.
const patterns = `"${SYNCED_SCHEMA}".lb_wafer_patterns`;
const dies = `"${SYNCED_SCHEMA}".lb_wafer_map_dies`;

export function registerOpsRoutes(app, getUserInfo) {
  let schemaReady = null;
  const ready = () => {
    if (!lakebaseConfigured()) {
      const e = new Error('Lakebase is not attached to this app');
      e.status = 503;
      throw e;
    }
    schemaReady ||= ensureSchema().catch((e) => {
      schemaReady = null;
      throw e;
    });
    return schemaReady;
  };

  // Queue of wafers with a systematic pattern, with their latest disposition
  app.get('/api/ops/wafers', async (req, res) => {
    try {
      await ready();
      const t0 = Date.now();
      const { pattern = '', site = '', status = 'open' } = req.query;
      const { rows } = await query(
        `SELECT p.wafer_id, p.lot_id, p.site, p.product, p.tester_id, p.probe_card_id,
                p.sort_date::text AS sort_date, p.pattern_class, p.likely_cause,
                p.wafer_yield::float AS wafer_yield, p.edge_fail_rate::float AS edge_fail_rate,
                d.disposition, d.entered_by, d.entered_at
           FROM ${patterns} p
           LEFT JOIN LATERAL (
             SELECT disposition, entered_by, entered_at FROM wafer_ops.wafer_dispositions w
              WHERE w.wafer_id = p.wafer_id ORDER BY entered_at DESC LIMIT 1
           ) d ON TRUE
          WHERE p.pattern_class NOT IN ('None', 'Random')
            AND ($1 = '' OR p.pattern_class = $1)
            AND ($2 = '' OR p.site = $2)
            AND ($3 <> 'open' OR d.disposition IS NULL OR d.disposition = 'HOLD')
          ORDER BY p.sort_date DESC, p.wafer_yield ASC
          LIMIT 300`,
        [pattern, site, status]
      );
      res.json({ wafers: rows, latencyMs: Date.now() - t0 });
    } catch (e) { sendError(res, e); }
  });

  // One wafer: sort context, die map and disposition history
  app.get('/api/ops/wafers/:waferId', async (req, res) => {
    try {
      await ready();
      const t0 = Date.now();
      const id = req.params.waferId;
      const [w, m, h] = await Promise.all([
        query(`SELECT wafer_id, lot_id, site, product, foundry, tester_id, probe_card_id, handler_id,
                      sort_date::text AS sort_date, pattern_class, likely_cause, reviewed_pattern,
                      dies_tested, dies_pass, wafer_yield::float AS wafer_yield,
                      edge_fail_rate::float AS edge_fail_rate, center_fail_rate::float AS center_fail_rate
                 FROM ${patterns} WHERE wafer_id = $1`, [id]),
        query(`SELECT die_x, die_y, bin_label FROM ${dies} WHERE wafer_id = $1`, [id]),
        query(`SELECT disposition, note, entered_by, entered_at FROM wafer_ops.wafer_dispositions
                WHERE wafer_id = $1 ORDER BY entered_at DESC`, [id]),
      ]);
      if (!w.rows.length) return res.status(404).json({ error: `Wafer ${id} not found` });
      res.json({ wafer: w.rows[0], dies: m.rows, history: h.rows, latencyMs: Date.now() - t0 });
    } catch (e) { sendError(res, e); }
  });

  // Record a disposition (OLTP write-back to Lakebase)
  app.post('/api/ops/wafers/:waferId/disposition', async (req, res) => {
    try {
      await ready();
      const { disposition, note = '' } = req.body || {};
      if (!DISPOSITIONS.includes(disposition)) {
        return res.status(400).json({ error: `disposition must be one of ${DISPOSITIONS.join(', ')}` });
      }
      const id = req.params.waferId;
      const user = getUserInfo(req);
      const { rows } = await query(
        `INSERT INTO wafer_ops.wafer_dispositions
                (wafer_id, disposition, note, pattern_class, tester_id, probe_card_id, entered_by)
         SELECT wafer_id, $2, NULLIF($3, ''), pattern_class, tester_id, probe_card_id, $4
           FROM ${patterns} WHERE wafer_id = $1
         RETURNING disposition, note, entered_by, entered_at`,
        [id, disposition, String(note).slice(0, 1000), user.email]
      );
      if (!rows.length) return res.status(404).json({ error: `Wafer ${id} not found` });
      res.json(rows[0]);
    } catch (e) { sendError(res, e); }
  });

  // ---------- Data pipeline (Lakeflow Job) ----------
  async function jobsApi(path, init = {}) {
    const resp = await fetch(`${workspaceHost()}${path}`, {
      ...init,
      headers: { Authorization: `Bearer ${await getServicePrincipalToken()}`, 'Content-Type': 'application/json' },
    });
    const text = await resp.text();
    if (!resp.ok) {
      const e = new Error(`Jobs API ${resp.status}: ${text.slice(0, 300)}`);
      e.status = resp.status;
      throw e;
    }
    return text ? JSON.parse(text) : {};
  }

  app.get('/api/pipeline/status', async (_req, res) => {
    try {
      if (!REFRESH_JOB_ID) return res.status(503).json({ error: 'Refresh job is not attached to this app' });
      const [job, runs] = await Promise.all([
        jobsApi(`/api/2.2/jobs/get?job_id=${REFRESH_JOB_ID}`),
        jobsApi(`/api/2.2/jobs/runs/list?job_id=${REFRESH_JOB_ID}&limit=5&expand_tasks=true`),
      ]);
      res.json({
        job: {
          id: REFRESH_JOB_ID,
          name: job.settings?.name,
          url: `${workspaceHost()}/jobs/${REFRESH_JOB_ID}`,
          tasks: (job.settings?.tasks || []).map((t) => ({ key: t.task_key, dependsOn: (t.depends_on || []).map((d) => d.task_key) })),
        },
        runs: (runs.runs || []).map((r) => ({
          runId: r.run_id,
          url: r.run_page_url,
          state: r.status?.state || r.state?.life_cycle_state,
          result: r.status?.termination_details?.code || r.state?.result_state || null,
          startTime: r.start_time,
          endTime: r.end_time || null,
          tasks: (r.tasks || []).map((t) => ({
            key: t.task_key,
            state: t.status?.state || t.state?.life_cycle_state,
            result: t.status?.termination_details?.code || t.state?.result_state || null,
          })),
        })),
      });
    } catch (e) { sendError(res, e); }
  });

  app.post('/api/pipeline/run', async (_req, res) => {
    try {
      if (!REFRESH_JOB_ID) return res.status(503).json({ error: 'Refresh job is not attached to this app' });
      const active = await jobsApi(`/api/2.2/jobs/runs/list?job_id=${REFRESH_JOB_ID}&active_only=true`);
      if ((active.runs || []).length) {
        return res.status(409).json({ error: 'A refresh is already running', runId: active.runs[0].run_id });
      }
      const run = await jobsApi('/api/2.2/jobs/run-now', { method: 'POST', body: JSON.stringify({ job_id: Number(REFRESH_JOB_ID) }) });
      res.json({ runId: run.run_id });
    } catch (e) { sendError(res, e); }
  });
}
