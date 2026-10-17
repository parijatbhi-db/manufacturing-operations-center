// Lakebase (Postgres) access for the Manufacturing Operations Center.
// Databricks Apps injects PGHOST / PGPORT / PGDATABASE / PGUSER / PGSSLMODE / LAKEBASE_ENDPOINT
// when the app has a `postgres` resource. The password is a short-lived OAuth database
// credential generated for the app's service principal.

import pg from 'pg';
import { getWorkspaceHost as workspaceHost } from './common.js';

const TOKEN_REFRESH_MARGIN_MS = 5 * 60 * 1000;

// Cache a token and share one in-flight request between concurrent callers, so a burst of new
// pool connections (e.g. the 3 parallel wafer-detail queries on a cold start) fetches it once.
function cachedToken(fetchToken) {
  let cached = null;
  let inFlight = null;
  return async () => {
    if (cached && cached.expiresAt - Date.now() > TOKEN_REFRESH_MARGIN_MS) return cached.value;
    inFlight ||= fetchToken()
      .then((t) => { cached = t; return t.value; })
      .finally(() => { inFlight = null; });
    return inFlight;
  };
}

// ---- Service principal OAuth (client credentials) ----
export const getServicePrincipalToken = cachedToken(async () => {
  const clientId = process.env.DATABRICKS_CLIENT_ID;
  const clientSecret = process.env.DATABRICKS_CLIENT_SECRET;
  if (!clientId || !clientSecret) throw new Error('App service principal credentials are not available');
  const resp = await fetch(`${workspaceHost()}/oidc/v1/token`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      Authorization: `Basic ${Buffer.from(`${clientId}:${clientSecret}`).toString('base64')}`,
    },
    body: 'grant_type=client_credentials&scope=all-apis',
  });
  if (!resp.ok) throw new Error(`SP token request failed: ${resp.status} ${await resp.text()}`);
  const body = await resp.json();
  return { value: body.access_token, expiresAt: Date.now() + body.expires_in * 1000 };
});

// ---- Lakebase database credential ----
const getDatabaseCredential = cachedToken(async () => {
  const resp = await fetch(`${workspaceHost()}/api/2.0/postgres/credentials`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${await getServicePrincipalToken()}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ endpoint: process.env.LAKEBASE_ENDPOINT }),
  });
  if (!resp.ok) throw new Error(`Lakebase credential request failed: ${resp.status} ${await resp.text()}`);
  const body = await resp.json();
  const expiresAt = body.expire_time ? Date.parse(body.expire_time) : Date.now() + 55 * 60 * 1000;
  return { value: body.token, expiresAt };
});

const REQUIRED_ENV = ['PGHOST', 'PGDATABASE', 'PGUSER', 'LAKEBASE_ENDPOINT'];

// Names of required Lakebase env vars that are not set (empty when fully configured)
export function missingLakebaseEnv() {
  return REQUIRED_ENV.filter((k) => !process.env[k]);
}

export function lakebaseConfigured() {
  return missingLakebaseEnv().length === 0;
}

let pool = null;

export function getPool() {
  if (!pool) {
    pool = new pg.Pool({
      host: process.env.PGHOST,
      port: Number(process.env.PGPORT || 5432),
      database: process.env.PGDATABASE,
      user: process.env.PGUSER,
      // pg calls this for every new connection, so tokens rotate without restarting the app
      password: () => getDatabaseCredential(),
      // Verify the server certificate (Lakebase hosts use publicly trusted certs); the password
      // sent over this connection is a live OAuth credential.
      ssl: { rejectUnauthorized: true },
      max: 5,
      idleTimeoutMillis: 30000,
    });
    pool.on('error', (err) => console.error('[lakebase] idle client error', err.message));
  }
  return pool;
}

// Lakebase computes scale to zero; retry once on a dropped/refused connection while it wakes.
export async function query(text, params = []) {
  try {
    return await getPool().query(text, params);
  } catch (err) {
    if (['ECONNRESET', 'ECONNREFUSED', '57P01'].includes(err.code)) {
      return getPool().query(text, params);
    }
    throw err;
  }
}

// App-owned write-back schema. The app's service principal creates (and therefore owns) it.
export async function ensureSchema() {
  await query(`CREATE SCHEMA IF NOT EXISTS wafer_ops`);
  await query(`
    CREATE TABLE IF NOT EXISTS wafer_ops.wafer_dispositions (
      id            BIGSERIAL PRIMARY KEY,
      wafer_id      TEXT        NOT NULL,
      disposition   TEXT        NOT NULL CHECK (disposition IN ('HOLD', 'REPROBE', 'RELEASE', 'SCRAP')),
      note          TEXT,
      pattern_class TEXT,
      tester_id     TEXT,
      probe_card_id TEXT,
      entered_by    TEXT        NOT NULL,
      entered_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )`);
  await query(`CREATE INDEX IF NOT EXISTS wafer_dispositions_wafer_idx
               ON wafer_ops.wafer_dispositions (wafer_id, entered_at DESC)`);
}
