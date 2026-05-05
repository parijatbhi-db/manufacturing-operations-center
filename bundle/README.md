# kari_semi_stdf - Databricks Asset Bundle

## Overview
CPO at KARI Semiconductor Ops needs to ingest STDF wafer sort/final test files, standardize them into analytics-ready schemas, and monitor yield, bin distributions, and tester utilization. Between 2025-08-18 and 2025-08-27 a new probe card revision and handler firmware update at the Austin site degraded contact quality on product MX-7 in lots from foundry F12, triggering a visible first-pass yield drop (-6.8 pts vs baseline), scrap/reticle rework, and overtime costs. Dashboards tie anomaly in STDF-derived metrics -> failing param/bin mix and site-equipment scope -> dated change logs -> quantified business impact in die loss and $ throughput.

## Deployment

This bundle can be deployed to any Databricks workspace using Databricks Asset Bundles (DAB):

### Prerequisites
1. **Databricks CLI**: Install the latest version
   ```bash
   pip install databricks-cli
   ```
2. **Authentication**: Configure your workspace credentials
   ```bash
   databricks configure
   ```
3. **Workspace Access**: Ensure you have permissions for:
   - Unity Catalog catalog/schema creation
   - SQL Warehouse access
   - Workspace file storage

### Deploy the Bundle
```bash
# Navigate to the dab directory
cd dab/

# Validate the bundle configuration
databricks bundle validate

# Deploy to your workspace (--force-lock to override any existing locks)
databricks bundle deploy --force-lock

# Run the data generation workflow
databricks bundle run demo_workflow
```

The deployment will:
1. Create Unity Catalog resources (schema and volume)
2. Upload PDF files to workspace (if applicable)
3. Deploy job and dashboard resources

The workflow will:
1. Create Unity Catalog catalog if it doesn't exist (DAB doesn't support catalog creation)
2. Generate synthetic data using Faker and write to Unity Catalog Volume
3. Execute SQL transformations (bronze → silver → gold)
4. Deploy agent bricks (Genie spaces, Knowledge Assistants, Multi-Agent Supervisors) if configured

## Bundle Contents

### Core Files
- `databricks.yml` - Asset bundle configuration defining jobs, dashboards, and deployment settings
- `bricks_conf.json` - Agent brick configurations (Genie/KA/MAS) if applicable
- `agent_bricks_service.py` - Service for managing agent brick resources (includes type definitions)
- `deploy_resources.py` - Script to recreate agent bricks in the target workspace

### Data Generation
- Python scripts using Faker library for realistic synthetic data
- Configurable row counts, schemas, and business logic
- Automatic Delta table creation in Unity Catalog

### SQL Transformations
- `transformations.sql` - SQL transformations for data processing
- Bronze (raw) → Silver (cleaned) → Gold (aggregated) medallion architecture
- Views and tables for business analytics

### Agent Bricks
This bundle includes AI agent resources:

- **Knowledge Assistant** (ID: `bca212ae-309e-4960-9bde-22916e6cce6b`)
  - AI assistant with knowledge sources from Unity Catalog volumes
  - Vector search-powered retrieval augmented generation (RAG)
  - Example questions and guidelines included

### Dashboards
This bundle includes Lakeview dashboards:
- **Semiconductor Test Quality and Throughput** - Business intelligence dashboard with visualizations

### PDF Documents
No PDF documents are included in this demo.

## Configuration

### Unity Catalog
- **Catalog**: `demo_generator`
- **Schema**: `kari_ross_kari_semi_stdf`
- **Workspace Path**: `/Users/kari.ross@databricks.com/kari_semi_stdf`

### Customization
You can modify the bundle by editing `databricks.yml`:
- Change target catalog/schema in the `variables` section
- Adjust cluster specifications for data generation
- Add additional tasks or resources

## Key Questions This Demo Answers
1. When did the FPY anomaly begin for MX-7 at AUS and what was the peak deviation from baseline?
2. Which hard bins and parameters contributed most to the yield loss, and how did their distributions shift during 2025-08-18..2025-08-27?
3. Is the issue isolated to specific testers, probe cards, or lots (e.g., F12 W27-W29), and do other sites show normal behavior?
4. Which dated change log entries align with onset and recovery, and what was rolled back to stabilize?
5. What was the total die loss and incremental cost, and how did retest and UPH trends affect cycle time and shipment schedules?
6. After rollback, how quickly did FPY and parameter z-scores return to baseline, and are any residual drifts present by 2025-09-01?

## Deployment to New Workspaces

This bundle is **portable** and can be deployed to any Databricks workspace:

1. The bundle will recreate all resources in the target workspace
2. Agent bricks (Genie/KA/MAS) are recreated from saved configurations in `bricks_conf.json`
3. SQL transformations and data generation scripts are environment-agnostic
4. Dashboards are deployed as Lakeview dashboard definitions

Simply run `databricks bundle deploy` in any workspace where you have the required permissions.

## Troubleshooting

### Common Issues

**Bundle validation fails:**
- Ensure `databricks.yml` has valid YAML syntax
- Check that catalog and schema names are valid
- Verify warehouse lookup matches an existing warehouse

**Agent brick deployment fails:**
- Check that `bricks_conf.json` exists and contains valid configurations
- Ensure you have permissions to create Genie spaces, KA tiles, and MAS tiles
- Verify vector search endpoint exists for Knowledge Assistants

**SQL transformations fail:**
- Ensure the catalog and schema exist in the target workspace
- Check warehouse permissions and availability
- Review SQL syntax for Unity Catalog compatibility (3-level namespace: `catalog.schema.table`)

### Getting Help
- Review Databricks Asset Bundles documentation: https://docs.databricks.com/dev-tools/bundles/
- Check the generated code in this bundle for implementation details
- Contact your Databricks workspace administrator for permissions issues

## Generated with AI Demo Generator
🤖 This bundle was automatically created using the Databricks AI Demo Generator.

**Created**: 2025-10-09 17:22:44
**User**: kari.ross@databricks.com
