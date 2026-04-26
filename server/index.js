// Manufacturing Operations Center — Express backend
// Proxies authenticated calls to Genie API and the Mosaic AI Agent serving endpoint.
// Uses on-behalf-of-user (OBO) tokens forwarded by Databricks Apps via X-Forwarded-Access-Token.

import express from 'express';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = process.env.PORT || 8000;
const NODE_ENV = process.env.NODE_ENV || 'development';

const DASHBOARD_ID = process.env.DASHBOARD_ID || '01f13ffb2116152b9c57017ac6989369';
const GENIE_SPACE_ID = process.env.GENIE_SPACE_ID || '01f1403396011f339af2cb207b69e9b6';
const AGENT_SERVING_ENDPOINT = process.env.AGENT_SERVING_ENDPOINT || 'mas-abac7793-endpoint';
const WORKSPACE_ORG_ID = process.env.WORKSPACE_ORG_ID || '1444828305810485';

// In Databricks Apps, DATABRICKS_HOST is hostname only (no scheme).
function getWorkspaceHost() {
  let host = process.env.DATABRICKS_HOST || 'e2-demo-field-eng.cloud.databricks.com';
  if (!host.startsWith('http')) host = `https://${host}`;
  return host;
}

// Pull the user's OBO token from the Databricks Apps proxy header.
// Locally (dev) we fall back to DATABRICKS_TOKEN if present.
function getUserToken(req) {
  const obo = req.header('x-forwarded-access-token');
  if (obo) return obo;
  if (process.env.DATABRICKS_TOKEN) return process.env.DATABRICKS_TOKEN;
  return null;
}

function getUserInfo(req) {
  return {
    email: req.header('x-forwarded-email') || req.header('x-forwarded-user') || 'unknown@databricks.com',
    name: req.header('x-forwarded-preferred-username') || req.header('x-forwarded-user') || 'Unknown User',
  };
}

const app = express();
app.use(express.json({ limit: '2mb' }));

// ---------- Health & config ----------
app.get('/api/health', (_req, res) => {
  res.json({ status: 'ok', env: NODE_ENV });
});

app.get('/api/config', (req, res) => {
  const user = getUserInfo(req);
  res.json({
    user,
    dashboard: {
      id: DASHBOARD_ID,
      embedUrl: `${getWorkspaceHost()}/embed/dashboardsv3/${DASHBOARD_ID}?o=${WORKSPACE_ORG_ID}`,
      publishedUrl: `${getWorkspaceHost()}/dashboardsv3/${DASHBOARD_ID}/published?o=${WORKSPACE_ORG_ID}`,
    },
    genie: {
      spaceId: GENIE_SPACE_ID,
      url: `${getWorkspaceHost()}/genie/rooms/${GENIE_SPACE_ID}?o=${WORKSPACE_ORG_ID}`,
    },
    agent: {
      endpoint: AGENT_SERVING_ENDPOINT,
    },
    workspace: {
      host: getWorkspaceHost(),
      orgId: WORKSPACE_ORG_ID,
    },
  });
});

// ---------- Generic Databricks fetch helper ----------
async function databricksFetch(req, pathSuffix, init = {}) {
  const token = getUserToken(req);
  if (!token) {
    const err = new Error('No Databricks user token. In local dev, set DATABRICKS_TOKEN.');
    err.status = 401;
    throw err;
  }
  const url = `${getWorkspaceHost()}${pathSuffix}`;
  const headers = {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
    ...(init.headers || {}),
  };
  const resp = await fetch(url, { ...init, headers });
  const text = await resp.text();
  let body;
  try { body = text ? JSON.parse(text) : {}; } catch { body = { raw: text }; }
  if (!resp.ok) {
    const err = new Error(body?.message || body?.error_code || `Databricks API ${resp.status}`);
    err.status = resp.status;
    err.body = body;
    throw err;
  }
  return body;
}

function sendError(res, err) {
  console.error('[error]', err.status || 500, err.message, err.body || '');
  res.status(err.status || 500).json({
    error: err.message,
    detail: err.body || null,
  });
}

// ---------- Genie ----------
// Start a new conversation with the first user message.
app.post('/api/genie/conversations', async (req, res) => {
  try {
    const { content } = req.body || {};
    if (!content) return res.status(400).json({ error: 'content is required' });
    const out = await databricksFetch(
      req,
      `/api/2.0/genie/spaces/${GENIE_SPACE_ID}/start-conversation`,
      { method: 'POST', body: JSON.stringify({ content }) }
    );
    res.json(out);
  } catch (e) { sendError(res, e); }
});

// Send a follow-up message in an existing conversation.
app.post('/api/genie/conversations/:conversationId/messages', async (req, res) => {
  try {
    const { content } = req.body || {};
    if (!content) return res.status(400).json({ error: 'content is required' });
    const { conversationId } = req.params;
    const out = await databricksFetch(
      req,
      `/api/2.0/genie/spaces/${GENIE_SPACE_ID}/conversations/${conversationId}/messages`,
      { method: 'POST', body: JSON.stringify({ content }) }
    );
    res.json(out);
  } catch (e) { sendError(res, e); }
});

// Poll a single message (used by client to watch for COMPLETED status).
app.get('/api/genie/conversations/:conversationId/messages/:messageId', async (req, res) => {
  try {
    const { conversationId, messageId } = req.params;
    const out = await databricksFetch(
      req,
      `/api/2.0/genie/spaces/${GENIE_SPACE_ID}/conversations/${conversationId}/messages/${messageId}`
    );
    res.json(out);
  } catch (e) { sendError(res, e); }
});

// Fetch attachment query result (Genie returns table results via attachments).
app.get('/api/genie/conversations/:conversationId/messages/:messageId/attachments/:attachmentId/query-result', async (req, res) => {
  try {
    const { conversationId, messageId, attachmentId } = req.params;
    const out = await databricksFetch(
      req,
      `/api/2.0/genie/spaces/${GENIE_SPACE_ID}/conversations/${conversationId}/messages/${messageId}/query-result/${attachmentId}`
    );
    res.json(out);
  } catch (e) { sendError(res, e); }
});

// ---------- Supervisor Agent (Mosaic AI / agent/v1/responses) ----------
app.post('/api/agent/chat', async (req, res) => {
  try {
    const { messages } = req.body || {};
    if (!Array.isArray(messages) || messages.length === 0) {
      return res.status(400).json({ error: 'messages array is required' });
    }
    // The endpoint task is agent/v1/responses, which expects {input: [...]}.
    // We accept OpenAI-style messages from the client and translate.
    const input = messages.map((m) => ({ role: m.role, content: m.content }));
    const out = await databricksFetch(
      req,
      `/serving-endpoints/${AGENT_SERVING_ENDPOINT}/invocations`,
      { method: 'POST', body: JSON.stringify({ input }) }
    );

    // Normalize response into a single string for the chat UI.
    let text = '';
    if (Array.isArray(out.output)) {
      for (const item of out.output) {
        if (item?.type === 'message' && Array.isArray(item.content)) {
          for (const c of item.content) {
            if (c?.type === 'output_text' && c.text) text += c.text;
          }
        }
      }
    }
    if (!text && out?.choices?.[0]?.message?.content) text = out.choices[0].message.content;
    res.json({ text, raw: out });
  } catch (e) { sendError(res, e); }
});

// ---------- Static frontend ----------
const clientDist = path.join(__dirname, '..', 'client', 'dist');
app.use(express.static(clientDist));

// SPA fallback — anything not /api/* serves index.html
app.get(/^\/(?!api\/).*/, (_req, res) => {
  res.sendFile(path.join(clientDist, 'index.html'), (err) => {
    if (err) res.status(404).send('Frontend not built. Run `npm run build`.');
  });
});

app.listen(PORT, () => {
  console.log(`[Manufacturing Operations Center] listening on :${PORT} (env=${NODE_ENV})`);
  console.log(`  Dashboard: ${DASHBOARD_ID}`);
  console.log(`  Genie space: ${GENIE_SPACE_ID}`);
  console.log(`  Agent endpoint: ${AGENT_SERVING_ENDPOINT}`);
});
