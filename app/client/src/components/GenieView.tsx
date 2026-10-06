import { useEffect, useRef, useState } from 'react';
import {
  AppConfig,
  GenieAttachment,
  GenieMessage,
  genieFollowup,
  genieGetMessage,
  genieGetQueryResult,
  genieStart,
} from '../lib/api';
import { IconExternal, IconGenie, IconSend } from './Icons';

type DisplayMsg = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  sql?: string;
  table?: { columns: string[]; rows: any[][] } | null;
  status?: string;
  error?: string;
};

const SUGGESTIONS = [
  'What is the average First Pass Yield over the last 14 days?',
  'Show me daily test yield by site for the last 30 days.',
  'Which failure bins are most common this week?',
  'Compare retest rate across foundries.',
];

function flattenAttachments(msg: GenieMessage): { text: string; sql?: string } {
  let text = msg.content || '';
  let sql: string | undefined;
  if (Array.isArray(msg.attachments)) {
    for (const a of msg.attachments as GenieAttachment[]) {
      if (a.text?.content) {
        text = text ? `${text}\n\n${a.text.content}` : a.text.content;
      }
      if (a.query?.query) sql = a.query.query;
      if (a.query?.description && !text) text = a.query.description;
    }
  }
  return { text, sql };
}

function tableFromQueryResult(result: any): { columns: string[]; rows: any[][] } | null {
  // Genie /query-result returns either { statement_response: {...} } or a direct manifest.
  const sr = result?.statement_response || result;
  const schema = sr?.manifest?.schema?.columns || sr?.schema?.columns;
  const data = sr?.result?.data_array || sr?.data_array;
  if (!schema || !data) return null;
  const columns = schema.map((c: any) => c.name);
  return { columns, rows: data };
}

async function pollUntilDone(conversationId: string, messageId: string): Promise<GenieMessage> {
  // Poll every 1.2s up to ~90s.
  const start = Date.now();
  while (Date.now() - start < 90_000) {
    const m = await genieGetMessage(conversationId, messageId);
    if (m.status === 'COMPLETED' || m.status === 'FAILED' || m.status === 'CANCELLED') return m;
    await new Promise((r) => setTimeout(r, 1200));
  }
  throw new Error('Genie timed out after 90s');
}

export default function GenieView({ config }: { config: AppConfig }) {
  const [messages, setMessages] = useState<DisplayMsg[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const streamRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, busy]);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setInput('');
    setBusy(true);

    const userMsg: DisplayMsg = { id: `u-${Date.now()}`, role: 'user', text: trimmed };
    const placeholder: DisplayMsg = {
      id: `a-${Date.now()}`,
      role: 'assistant',
      text: 'Genie is thinking',
      status: 'thinking',
    };
    setMessages((prev) => [...prev, userMsg, placeholder]);

    try {
      let convId = conversationId;
      let messageId: string;

      if (!convId) {
        const start = await genieStart(trimmed);
        convId = start.conversation_id;
        messageId = start.message_id;
        setConversationId(convId);
      } else {
        const followup = await genieFollowup(convId, trimmed);
        messageId = followup.message_id;
      }

      const final = await pollUntilDone(convId, messageId);

      if (final.status === 'FAILED') {
        // First line of the SQL error is the readable part; the rest is a query plan dump.
        const detail = (final.error?.error || final.error?.message || '').split('\n')[0].slice(0, 300);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === placeholder.id
              ? { ...m, text: detail ? `Genie returned an error: ${detail}` : 'Genie returned an error.', status: 'error', error: 'failed' }
              : m
          )
        );
        return;
      }

      const { text: answerText, sql } = flattenAttachments(final);
      let table: { columns: string[]; rows: any[][] } | null = null;

      // If there's a query attachment with a statement_id, fetch the result.
      const queryAttachment = (final.attachments || []).find((a) => a.query);
      if (queryAttachment) {
        try {
          const qr = await genieGetQueryResult(convId, messageId, queryAttachment.attachment_id);
          table = tableFromQueryResult(qr);
        } catch (e) {
          // non-fatal
          console.warn('query-result fetch failed', e);
        }
      }

      setMessages((prev) =>
        prev.map((m) =>
          m.id === placeholder.id
            ? {
                ...m,
                text: answerText || 'Done.',
                sql,
                table,
                status: 'done',
              }
            : m
        )
      );
    } catch (e: any) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === placeholder.id
            ? { ...m, text: e?.message || 'Request failed.', status: 'error', error: 'failed' }
            : m
        )
      );
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  return (
    <>
      <div className="view-header">
        <div>
          <h1 className="view-title">Genie — STDF Test Quality Analytics</h1>
          <div className="view-subtitle">
            Ask data questions in natural language. Backed by metric views over yield, bins, and throughput.
          </div>
        </div>
        <div className="view-actions">
          <a className="btn" href={config.genie.url} target="_blank" rel="noreferrer">
            Open in workspace <IconExternal />
          </a>
        </div>
      </div>

      <div className="view-body">
        <div className="chat-shell">
          <div className="chat-stream" ref={streamRef}>
            {messages.length === 0 ? (
              <div className="empty-state">
                <div className="empty-mark"><IconGenie size={28} /></div>
                <h2>Start a Genie conversation</h2>
                <p>Ask about yield, retest rate, throughput, or failure bins. Genie will write SQL against the metric views and return results.</p>
                <div className="suggestions">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} className="suggestion" onClick={() => send(s)}>{s}</button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((m) => (
                <div className={`message ${m.role}`} key={m.id}>
                  <div className={`bubble-avatar ${m.role}`}>{m.role === 'user' ? 'You' : 'G'}</div>
                  <div className={`bubble ${m.status === 'thinking' ? 'thinking' : ''} ${m.error ? 'error' : ''}`}>
                    {m.status === 'thinking' ? (
                      <span>Genie is thinking<span className="dots" /></span>
                    ) : (
                      <>
                        <div>{m.text}</div>
                        {m.sql && <div className="bubble-sql">{m.sql}</div>}
                        {m.table && m.table.columns.length > 0 && (
                          <div className="bubble-table-wrap">
                            <table className="bubble-table">
                              <thead>
                                <tr>{m.table.columns.map((c) => <th key={c}>{c}</th>)}</tr>
                              </thead>
                              <tbody>
                                {m.table.rows.slice(0, 200).map((row, i) => (
                                  <tr key={i}>{row.map((cell, j) => <td key={j}>{String(cell ?? '')}</td>)}</tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="composer">
            <div className="composer-inner">
              <textarea
                rows={1}
                placeholder="Ask Genie about yield, throughput, failure bins…"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                disabled={busy}
              />
              <button className="send-btn" disabled={busy || !input.trim()} onClick={() => send(input)}>
                {busy ? <span className="spin" /> : <IconSend />}
              </button>
            </div>
            <div className="composer-hint">Enter to send · Shift+Enter for newline</div>
          </div>
        </div>
      </div>
    </>
  );
}
