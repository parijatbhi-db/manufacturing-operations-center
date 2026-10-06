"""Refresh the Lakebase synced tables after the gold layer is rebuilt, then make sure the
Manufacturing Operations Center app's service principal can read them.

Runs as a job task (databricks-sdk>=0.81, pg8000). pg8000 is pure Python; psycopg's bundled
libpq aborts (SIGABRT) intermittently on serverless job compute. Each synced table is backed by a
SNAPSHOT pipeline; this starts an update on each one and waits for it to finish.
"""

import argparse
import logging
import ssl
import time

import pg8000.native as pgn
from databricks.sdk import WorkspaceClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

PIPELINE_TIMEOUT_S = 30 * 60


def wait_for_update(w: WorkspaceClient, pipeline_id: str, update_id: str) -> str:
    deadline = time.time() + PIPELINE_TIMEOUT_S
    while time.time() < deadline:
        state = w.pipelines.get_update(pipeline_id=pipeline_id, update_id=update_id).update.state.value
        if state in ('COMPLETED', 'FAILED', 'CANCELED'):
            return state
        time.sleep(15)
    return 'TIMEOUT'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--synced-tables', required=True, help='Comma-separated UC names of synced tables')
    parser.add_argument('--endpoint', required=True, help='projects/<p>/branches/<b>/endpoints/<e>')
    parser.add_argument('--database', required=True)
    parser.add_argument('--schema', required=True, help='Postgres schema holding the synced tables')
    parser.add_argument('--app-name', required=True)
    args, _ = parser.parse_known_args()

    w = WorkspaceClient()

    # 1. Snapshot-refresh every synced table
    failed = []
    for name in [t.strip() for t in args.synced_tables.split(',') if t.strip()]:
        st = w.postgres.get_synced_table(name=f'synced_tables/{name}')
        pipeline_id = st.status.pipeline_id
        logger.info(f'Refreshing {name} (pipeline {pipeline_id})')
        update_id = w.pipelines.start_update(pipeline_id=pipeline_id).update_id
        state = wait_for_update(w, pipeline_id, update_id)
        logger.info(f'{name}: {state}')
        if state != 'COMPLETED':
            failed.append(f'{name}={state}')

    # 2. Grant the app's service principal read access to the synced schema
    sp_client_id = w.apps.get(name=args.app_name).service_principal_client_id
    endpoint = w.postgres.get_endpoint(name=args.endpoint)
    token = w.postgres.generate_database_credential(endpoint=args.endpoint).token
    user = w.current_user.me().user_name
    conn = pgn.Connection(user=user, password=token, host=endpoint.status.hosts.host,
                          database=args.database, ssl_context=ssl.create_default_context())
    try:
        if conn.run('SELECT 1 FROM pg_roles WHERE rolname = :r', r=sp_client_id):
            schema, role = pgn.identifier(args.schema), pgn.identifier(sp_client_id)
            conn.run(f'GRANT USAGE ON SCHEMA {schema} TO {role}')
            conn.run(f'GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {role}')
            conn.run(f'ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO {role}')
            logger.info(f'Granted read on schema {args.schema} to app SP {sp_client_id}')
        else:
            logger.warning(f'Postgres role for app SP {sp_client_id} not found; attach the Lakebase '
                           'resource to the app and redeploy it, then rerun this task')
    finally:
        conn.close()

    if failed:
        raise RuntimeError(f'Synced table refresh failed: {", ".join(failed)}')


if __name__ == '__main__':
    main()
