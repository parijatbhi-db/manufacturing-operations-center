import { useEffect, useState } from 'react';
import { AppConfig, getConfig } from './lib/api';
import DashboardView from './components/DashboardView';
import GenieView from './components/GenieView';
import AgentView from './components/AgentView';
import { IconAgent, IconDashboard, IconFactory, IconGenie } from './components/Icons';

type Tab = 'dashboard' | 'genie' | 'agent';

function initials(name: string): string {
  if (!name) return '??';
  const parts = name.split(/[\s.@]+/).filter(Boolean);
  if (parts.length === 0) return '??';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

export default function App() {
  const [tab, setTab] = useState<Tab>('dashboard');
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getConfig().then(setConfig).catch((e) => setError(e.message || 'Failed to load config'));
  }, []);

  if (error) {
    return (
      <div className="full-loader">
        <div style={{ color: '#FF3621', fontWeight: 700 }}>Failed to load app config</div>
        <div>{error}</div>
      </div>
    );
  }
  if (!config) {
    return (
      <div className="full-loader">
        <span className="spin" /> Loading Manufacturing Operations Center…
      </div>
    );
  }

  const userName = config.user.name || config.user.email.split('@')[0];

  return (
    <div className="app-shell">
      {/* Top bar */}
      <header className="topbar">
        <div className="topbar-left">
          <div className="brand-mark"><IconFactory size={20} /></div>
          <div className="brand-text">
            <div className="brand-title">Manufacturing Operations Center</div>
            <div className="brand-subtitle">Industrial AI for Operations</div>
          </div>
        </div>
        <div className="topbar-right">
          <div className="user-pill">
            <div className="user-avatar">{initials(userName)}</div>
            <div className="user-meta">
              <span className="user-name">{userName}</span>
              <span className="user-email">{config.user.email}</span>
            </div>
          </div>
        </div>
      </header>

      {/* Body */}
      <div className="body">
        <aside className="sidebar">
          <div className="sidebar-section-label">Workspace</div>
          <button
            className={`nav-item ${tab === 'dashboard' ? 'active' : ''}`}
            onClick={() => setTab('dashboard')}
          >
            <span className="nav-icon"><IconDashboard /></span>Dashboard
          </button>
          <button
            className={`nav-item ${tab === 'genie' ? 'active' : ''}`}
            onClick={() => setTab('genie')}
          >
            <span className="nav-icon"><IconGenie /></span>Genie
          </button>
          <button
            className={`nav-item ${tab === 'agent' ? 'active' : ''}`}
            onClick={() => setTab('agent')}
          >
            <span className="nav-icon"><IconAgent /></span>Supervisor Agent
          </button>
          <div className="sidebar-footer">
            Powered by Databricks · {config.workspace.host.replace('https://', '')}
          </div>
        </aside>

        <main className="content">
          {tab === 'dashboard' && <DashboardView config={config} />}
          {tab === 'genie' && <GenieView config={config} />}
          {tab === 'agent' && <AgentView config={config} />}
        </main>
      </div>
    </div>
  );
}
