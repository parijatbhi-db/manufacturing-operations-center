# Databricks App `mfg-ops-center`

- URL: https://mfg-ops-center-1444828305810485.aws.databricksapps.com
- App status: **RUNNING** (App has status: App is running)
- Compute: **ACTIVE**
- Service principal client id: `fa545c4a-29ec-475f-8c7f-fb1a799b7ad4`
- User API scopes: dashboards.genie, serving.serving-endpoints

## Resources

| Key | Type | Target | Permission |
|---|---|---|---|
| genie-space | genie_space | `01f1403396011f339af2cb207b69e9b6` | CAN_RUN |
| serving-endpoint | serving_endpoint | `mas-40863b57-endpoint` | CAN_QUERY |
| lakebase | postgres | `projects/parijat-mfg-ops/branches/production` | CAN_CONNECT_AND_CREATE |
| refresh-job | job | `848871296768056` | CAN_MANAGE_RUN |

## Recent deployments

| Deployment | Status | Created |
|---|---|---|
| `01f1c29781cb1482abd85e2db582894b` | SUCCEEDED | 2026-10-07T21:38:59Z |
| `01f1c296e60311f39a7296e2efd7d91f` | SUCCEEDED | 2026-10-07T21:34:38Z |
| `01f1c296bda31ef4ace54f3b0ebb85ca` | FAILED | 2026-10-07T21:33:30Z |
| `01f1c1b48d351980989aa432cf186c56` | SUCCEEDED | 2026-10-06T18:34:23Z |
| `01f1c1218fe611a5b1d086d0b6dab2bc` | SUCCEEDED | 2026-10-06T01:02:11Z |
