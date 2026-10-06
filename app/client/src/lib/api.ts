// Thin API client that talks to the Express backend.

export type AppConfig = {
  user: { name: string; email: string };
  dashboard: { id: string; embedUrl: string; publishedUrl: string };
  genie: { spaceId: string; url: string };
  agent: { endpoint: string; tileId: string; workspaceUrl: string };
  knowledgeAssistant: { tileId: string; endpoint: string; workspaceUrl: string };
  workspace: { host: string; orgId: string };
};

export async function getConfig(): Promise<AppConfig> {
  const r = await fetch('/api/config');
  if (!r.ok) throw new Error(`config failed: ${r.status}`);
  return r.json();
}

// ---------- Genie ----------
export type GenieAttachment = {
  attachment_id: string;
  text?: { content: string };
  query?: { query: string; description?: string; statement_id?: string };
};

export type GenieMessage = {
  id: string;
  conversation_id: string;
  status: string; // SUBMITTED | IN_PROGRESS | COMPLETED | FAILED ...
  content?: string;
  attachments?: GenieAttachment[];
  query_result?: any;
  // Genie returns { error: '<SQL error text>', type: 'SQL_EXECUTION_EXCEPTION' }
  error?: { error?: string; message?: string; type?: string } | null;
};

export type GenieStartResponse = {
  message_id: string;
  conversation_id: string;
  message: GenieMessage;
  conversation: { id: string; title?: string };
};

export async function genieStart(content: string): Promise<GenieStartResponse> {
  const r = await fetch('/api/genie/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  if (!r.ok) throw new Error(`genie start failed: ${r.status} ${await r.text()}`);
  return r.json();
}

export async function genieFollowup(conversationId: string, content: string): Promise<{ message_id: string; message: GenieMessage }> {
  const r = await fetch(`/api/genie/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  if (!r.ok) throw new Error(`genie followup failed: ${r.status} ${await r.text()}`);
  return r.json();
}

export async function genieGetMessage(conversationId: string, messageId: string): Promise<GenieMessage> {
  const r = await fetch(`/api/genie/conversations/${conversationId}/messages/${messageId}`);
  if (!r.ok) throw new Error(`genie poll failed: ${r.status} ${await r.text()}`);
  return r.json();
}

export async function genieGetQueryResult(
  conversationId: string,
  messageId: string,
  attachmentId: string
): Promise<any> {
  const r = await fetch(
    `/api/genie/conversations/${conversationId}/messages/${messageId}/attachments/${attachmentId}/query-result`
  );
  if (!r.ok) throw new Error(`genie query-result failed: ${r.status} ${await r.text()}`);
  return r.json();
}

// ---------- Agent ----------
export type ChatMsg = { role: 'user' | 'assistant'; content: string };

export async function agentChat(messages: ChatMsg[]): Promise<{ text: string; raw: any }> {
  const r = await fetch('/api/agent/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
  });
  if (!r.ok) throw new Error(`agent chat failed: ${r.status} ${await r.text()}`);
  return r.json();
}

// ---------- Wafer Operations (Lakebase) ----------
export type OpsWafer = {
  wafer_id: string;
  lot_id: string;
  site: string;
  product: string;
  tester_id: string;
  probe_card_id: string;
  sort_date: string;
  pattern_class: string;
  likely_cause: string;
  wafer_yield: number;
  edge_fail_rate: number;
  disposition: string | null;
  entered_by: string | null;
  entered_at: string | null;
};

export type OpsWaferDetail = {
  wafer: OpsWafer & {
    foundry: string;
    handler_id: string;
    reviewed_pattern: string | null;
    dies_tested: number;
    dies_pass: number;
    center_fail_rate: number;
  };
  dies: { die_x: number; die_y: number; bin_label: string }[];
  history: { disposition: string; note: string | null; entered_by: string; entered_at: string }[];
  latencyMs: number;
};

async function jsonOrThrow(r: Response, what: string) {
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).error || msg; } catch { /* non-JSON body */ }
    throw new Error(`${what}: ${msg}`);
  }
  return r.json();
}

export async function opsListWafers(params: { pattern?: string; site?: string; status?: string }) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]);
  return jsonOrThrow(await fetch(`/api/ops/wafers?${q}`), 'wafer queue') as Promise<{ wafers: OpsWafer[]; latencyMs: number }>;
}

export async function opsGetWafer(waferId: string): Promise<OpsWaferDetail> {
  return jsonOrThrow(await fetch(`/api/ops/wafers/${encodeURIComponent(waferId)}`), 'wafer detail');
}

export async function opsSetDisposition(waferId: string, disposition: string, note: string) {
  return jsonOrThrow(
    await fetch(`/api/ops/wafers/${encodeURIComponent(waferId)}/disposition`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ disposition, note }),
    }),
    'save disposition'
  );
}

// ---------- Data pipeline (Lakeflow Job) ----------
export type PipelineRun = {
  runId: number;
  url: string;
  state: string;
  result: string | null;
  startTime: number;
  endTime: number | null;
  tasks: { key: string; state: string; result: string | null }[];
};

export type PipelineStatus = {
  job: { id: string; name: string; url: string; tasks: { key: string; dependsOn: string[] }[] };
  runs: PipelineRun[];
};

export async function pipelineStatus(): Promise<PipelineStatus> {
  return jsonOrThrow(await fetch('/api/pipeline/status'), 'pipeline status');
}

export async function pipelineRun(): Promise<{ runId: number }> {
  return jsonOrThrow(await fetch('/api/pipeline/run', { method: 'POST' }), 'start refresh');
}
