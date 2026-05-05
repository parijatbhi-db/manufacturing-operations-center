import { useEffect, useRef, useState } from 'react';
import { AppConfig, ChatMsg, agentChat } from '../lib/api';
import { IconAgent, IconExternal, IconSend } from './Icons';

type DisplayMsg = ChatMsg & { id: string; status?: 'thinking' | 'done' | 'error' };

const SUGGESTIONS = [
  'What is happening with yield at the affected sites today?',
  'Investigate the root cause of recent retest spikes.',
  'Summarize equipment changes that correlate with yield drops.',
  'Recommend next operational actions for the manufacturing team.',
];

export default function AgentView({ config }: { config: AppConfig }) {
  const [messages, setMessages] = useState<DisplayMsg[]>([]);
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

    const userMsg: DisplayMsg = { id: `u-${Date.now()}`, role: 'user', content: trimmed };
    const placeholder: DisplayMsg = {
      id: `a-${Date.now()}`,
      role: 'assistant',
      content: 'Supervisor agent is thinking',
      status: 'thinking',
    };

    const conversation = [...messages, userMsg].map(({ role, content }) => ({ role, content }));
    setMessages((prev) => [...prev, userMsg, placeholder]);

    try {
      const out = await agentChat(conversation as ChatMsg[]);
      setMessages((prev) =>
        prev.map((m) =>
          m.id === placeholder.id
            ? { ...m, content: out.text || '(empty response)', status: 'done' }
            : m
        )
      );
    } catch (e: any) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === placeholder.id
            ? { ...m, content: e?.message || 'Agent request failed.', status: 'error' }
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
          <h1 className="view-title">Supervisor Agent</h1>
          <div className="view-subtitle">
            Multi-agent supervisor for manufacturing operations · endpoint <code>{config.agent.endpoint}</code>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <a className="btn" href={config.knowledgeAssistant.workspaceUrl} target="_blank" rel="noreferrer">
            Knowledge Assistant <IconExternal />
          </a>
          <a className="btn" href={config.agent.workspaceUrl} target="_blank" rel="noreferrer">
            Open Supervisor in workspace <IconExternal />
          </a>
        </div>
      </div>

      <div className="view-body">
        <div className="chat-shell">
          <div className="chat-stream" ref={streamRef}>
            {messages.length === 0 ? (
              <div className="empty-state">
                <div className="empty-mark"><IconAgent size={28} /></div>
                <h2>Talk to the Supervisor Agent</h2>
                <p>This agent orchestrates specialist tools across the operation. Ask it open-ended manufacturing questions and it will route to the right expert.</p>
                <div className="suggestions">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} className="suggestion" onClick={() => send(s)}>{s}</button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((m) => (
                <div className={`message ${m.role}`} key={m.id}>
                  <div className={`bubble-avatar ${m.role === 'user' ? 'user' : 'assistant'}`}>
                    {m.role === 'user' ? 'You' : 'A'}
                  </div>
                  <div className={`bubble ${m.status === 'thinking' ? 'thinking' : ''} ${m.status === 'error' ? 'error' : ''}`}>
                    {m.status === 'thinking' ? (
                      <span>Supervisor agent is thinking<span className="dots" /></span>
                    ) : (
                      m.content
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
                placeholder="Ask the supervisor agent a question…"
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
