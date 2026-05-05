# Manufacturing Operations Center

A Databricks App that surfaces three workspace assets through a single UI for manufacturing operations teams.

**Deployed:** https://mfg-ops-center-1444828305810485.aws.databricksapps.com
**Workspace:** `e2-demo-field-eng.cloud.databricks.com`

## What it does

Three tabs, one app:

| Tab | Asset | How it's wired |
|---|---|---|
| Dashboard | Lakeview dashboard `01f13ffb...` (Semiconductor Test Quality and Throughput) | Iframe to `/embed/dashboardsv3/<id>` |
| Genie | Genie space `01f14033...` (STDF Semiconductor Test Quality Analytics) | Chat UI → backend proxies to `/api/2.0/genie/spaces/<id>` using OBO token |
| Supervisor Agent | Mosaic AI agent serving endpoint `mas-abac7793-endpoint` | Chat UI → backend proxies to `/serving-endpoints/<name>/invocations` using OBO token |

User identity is read from the `X-Forwarded-Email` / `X-Forwarded-Preferred-Username` headers injected by the Databricks Apps proxy. Workspace API calls use the `X-Forwarded-Access-Token` header for on-behalf-of auth.

## Stack

- **Frontend:** React + Vite + TypeScript
- **Backend:** Node.js + Express
- **Deploy target:** Databricks Apps (Node.js runtime)

## Layout

```
.
├── app.yaml              # Databricks App config: command, resources, scopes
├── package.json          # Root deps (express, node-fetch); build script chains into client/
├── server/
│   └── index.js          # Express app: serves client/dist + proxies /api/genie + /api/agent
└── client/
    ├── vite.config.ts
    ├── tsconfig.json
    ├── index.html
    └── src/
        ├── App.tsx
        ├── main.tsx
        ├── styles.css
        ├── lib/api.ts
        └── components/
            ├── DashboardView.tsx
            ├── GenieView.tsx
            ├── AgentView.tsx
            └── Icons.tsx
```

## Deploy

The Databricks Apps platform installs deps and builds the frontend server-side, so no local `npm install` is required.

```bash
databricks sync . /Workspace/Users/<you>/manufacturing-operations-center -p DEFAULT \
  --exclude node_modules --exclude client/node_modules --exclude client/dist --exclude .git

databricks apps deploy mfg-ops-center \
  --source-code-path /Workspace/Users/<you>/manufacturing-operations-center -p DEFAULT
```

## Configuration

`app.yaml` declares the resources and OAuth user-scopes:

- Resource `genie-space` → space `01f14033...`, `CAN_RUN`
- Resource `serving-endpoint` → `mas-abac7793-endpoint`, `CAN_QUERY`
- User-API scopes: `dashboards.genie`, `serving.serving-endpoints`

The dashboard ID is passed via env var (Databricks Apps doesn't have a `dashboard` resource type — viewers need `CAN_READ` on the dashboard directly).

## First-run

On first visit Databricks prompts for one-time OAuth consent for the Genie + serving-endpoint scopes. After that, all three tabs work with no further setup.
