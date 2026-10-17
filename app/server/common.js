// Helpers shared by every route module, so host resolution and error responses can't drift apart.

// In Databricks Apps, DATABRICKS_HOST is hostname only (no scheme).
export function getWorkspaceHost() {
  let host = process.env.DATABRICKS_HOST || 'e2-demo-field-eng.cloud.databricks.com';
  if (!host.startsWith('http')) host = `https://${host}`;
  return host;
}

export function sendError(res, err) {
  console.error('[error]', err.status || 500, err.message, err.body || '');
  res.status(err.status || 500).json({
    error: err.message,
    detail: err.body || null,
  });
}
