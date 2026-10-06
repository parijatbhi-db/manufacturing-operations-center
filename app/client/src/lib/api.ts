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
