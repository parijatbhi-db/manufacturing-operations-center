import { useState } from 'react';
import { AppConfig } from '../lib/api';
import { IconExternal, IconRefresh } from './Icons';

export default function DashboardView({ config }: { config: AppConfig }) {
  const [reloadKey, setReloadKey] = useState(0);
  return (
    <>
      <div className="view-header">
        <div>
          <h1 className="view-title">Semiconductor Test Quality and Throughput</h1>
          <div className="view-subtitle">
            Live AI/BI dashboard — yield, retest rate, throughput, and equipment health across sites.
          </div>
        </div>
        <div className="view-actions">
          <button className="btn" onClick={() => setReloadKey((k) => k + 1)}>
            <IconRefresh /> Reload
          </button>
          <a className="btn" href={config.dashboard.publishedUrl} target="_blank" rel="noreferrer">
            Open in workspace <IconExternal />
          </a>
        </div>
      </div>
      <div className="view-body">
        <div className="iframe-wrap">
          <iframe
            key={reloadKey}
            title="Lakeview Dashboard"
            src={config.dashboard.embedUrl}
            allow="clipboard-read; clipboard-write"
          />
        </div>
      </div>
    </>
  );
}
