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
| `01f1c1b48d351980989aa432cf186c56` | SUCCEEDED | 2026-10-06T18:34:23Z |
| `01f1c1218fe611a5b1d086d0b6dab2bc` | SUCCEEDED | 2026-10-06T01:02:11Z |
| `01f1c11e578d1b1c877c84b4779ad7b0` | SUCCEEDED | 2026-10-06T00:39:08Z |
| `01f1c11c7c7e1215a31a54705d1d9239` | SUCCEEDED | 2026-10-06T00:25:51Z |
| `01f1bba97f8d1db48de2dc01876eb697` | SUCCEEDED | 2026-09-29T02:00:08Z |
