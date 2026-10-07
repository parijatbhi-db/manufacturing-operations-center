"""Collect build evidence from the live workspace and write it into evidence/ as plain text.

    python evidence/collect_evidence.py --profile e2-demo-fe --job-run-id <run id of the refresh job>

1. Exports the given end-to-end run of the refresh job: task states and durations, each Python
   task's logged output, and the Lakeflow pipeline update it triggered -> runs/job_run_<id>.md
2. Runs build_evidence.py as a one-time serverless notebook job and exports the executed notebook
   with its outputs -> notebook/build_evidence.ipynb and notebook/build_evidence.md
3. Captures the bundle summary, app status and app logs -> deploy/
"""

import argparse
import base64
import html
import json
import re
import subprocess
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs, workspace

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
JOB_ID = 848871296768056
PIPELINE_ID = 'bb686d6e-f137-4d1b-ae5d-924ac9b6629a'
APP_NAME = 'mfg-ops-center'
NOTEBOOK_PATH = '/Users/parijat.bhide@databricks.com/semi_stdf/evidence/build_evidence'


def ts(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC') if ms else '-'


def dur(start, end):
    s = round(((end or start) - start) / 1000)
    return f'{s // 60}m {s % 60:02d}s'


# ---------------------------------------------------------------- 1. job run
def export_job_run(w: WorkspaceClient, run_id: int) -> Path:
    run = w.jobs.get_run(run_id=run_id)
    out = [f'# Refresh job run {run_id}', '',
           f'- Job: **{w.jobs.get(job_id=run.job_id).settings.name}** (`{run.job_id}`)',
           f'- Run page: {run.run_page_url}',
           f'- Result: **{run.state.result_state.value}** | started {ts(run.start_time)} | '
           f'ended {ts(run.end_time)} | duration {dur(run.start_time, run.end_time)}',
           f'- Trigger: {run.trigger.value if run.trigger else "-"}', '',
           '## Tasks', '', '| # | Task | Type | Attempt | Result | Started | Duration |',
           '|---|---|---|---|---|---|---|']
    tasks = sorted(run.tasks, key=lambda t: (t.start_time or 0, t.attempt_number or 0))
    for i, t in enumerate(tasks, 1):
        kind = next(k for k in ('spark_python_task', 'sql_task', 'pipeline_task', 'notebook_task')
                    if getattr(t, k, None))
        out.append(f'| {i} | `{t.task_key}` | {kind} | {t.attempt_number or 0} | '
                   f'{t.state.result_state.value if t.state.result_state else t.state.life_cycle_state.value} | '
                   f'{ts(t.start_time)} | {dur(t.start_time, t.end_time)} |')

    for t in tasks:
        out += ['', f'## `{t.task_key}`', '']
        if t.sql_task:
            out.append(f'SQL file `{t.sql_task.file.path}` on warehouse `{t.sql_task.warehouse_id}`, '
                       f'parameters `{json.dumps(t.sql_task.parameters)}`.')
        if t.pipeline_task:
            out += pipeline_update_section(w, t)
            continue
        try:
            o = w.jobs.get_run_output(run_id=t.run_id)
        except Exception as e:  # SQL tasks have no driver log
            out.append(f'_No run output available: {e}_')
            continue
        if o.error:
            out += ['**Error:**', '```', o.error.strip(), '```']
        if o.logs:
            out += ['Logged output' + (' (truncated by the Jobs API)' if o.logs_truncated else '') + ':',
                    '```', o.logs.rstrip(), '```']
        elif not o.error:
            out.append('_No logged output._')

    path = ROOT / 'runs' / f'job_run_{run_id}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(out) + '\n')
    return path


def pipeline_update_section(w, task):
    # Match the pipeline update to the task by its start time
    lines = [f'Lakeflow pipeline `{task.pipeline_task.pipeline_id}` '
             f'(full_refresh={task.pipeline_task.full_refresh}).', '']
    started, ended = task.start_time, task.end_time or int(time.time() * 1000)
    updates = [u for u in w.pipelines.list_updates(pipeline_id=PIPELINE_ID, max_results=25).updates
               if started - 60_000 <= u.creation_time <= ended]
    if not updates:
        return lines + ['_Pipeline update not found._']
    u = updates[-1]
    lines.append(f'Update `{u.update_id}`: **{u.state.value}**, created {ts(u.creation_time)}.')
    # The events API cannot filter on update id; page through recent events instead
    events = []
    for e in w.pipelines.list_pipeline_events(pipeline_id=PIPELINE_ID, max_results=250):
        if e.origin and e.origin.update_id == u.update_id:
            events.append(e)
        if e.timestamp and e.timestamp < datetime.fromtimestamp(u.creation_time / 1000, timezone.utc).isoformat():
            break
    lines += ['', 'Pipeline event log (oldest first):', '```']
    for e in reversed(events):
        lines.append(f'{e.timestamp}  {e.level.value:<5} {e.event_type:<22} {e.message}')
    lines.append('```')
    return lines


# ---------------------------------------------------------------- 2. notebook
def run_notebook(w: WorkspaceClient):
    src = (ROOT / 'build_evidence.py').read_bytes()
    w.workspace.mkdirs(NOTEBOOK_PATH.rsplit('/', 1)[0])
    w.workspace.import_(path=NOTEBOOK_PATH, content=base64.b64encode(src).decode(), overwrite=True,
                        format=workspace.ImportFormat.SOURCE, language=workspace.Language.PYTHON)
    run = w.jobs.submit_and_wait(
        run_name='[mfg_ops] build evidence',
        tasks=[jobs.SubmitTask(task_key='build_evidence',
                               notebook_task=jobs.NotebookTask(notebook_path=NOTEBOOK_PATH))],
        timeout=timedelta(minutes=30))
    task = run.tasks[0]
    print('notebook run', run.run_id, task.state.result_state)
    return task.run_id, run


def notebook_model(w: WorkspaceClient, task_run_id: int) -> dict:
    view = w.jobs.export_run(run_id=task_run_id, views_to_export=jobs.ViewsToExport.CODE).views[0]
    m = re.search(r"__DATABRICKS_NOTEBOOK_MODEL = '([^']+)'", view.content)
    return json.loads(urllib.parse.unquote(base64.b64decode(m.group(1)).decode()))


def cell_text(cmd: dict) -> str:
    """Plain-text output of an executed command."""
    r = cmd.get('results') or {}
    if r.get('type') == 'error' or cmd.get('error'):
        return (cmd.get('error') or r.get('data') or '').strip()
    data = r.get('data')
    if r.get('type') in ('html', 'htmlSandbox') and isinstance(data, str):
        # print() output is wrapped in <pre> by the notebook renderer
        data = html.unescape(re.sub(r'<[^>]+>', '', data))
    if isinstance(data, list):  # listResults: one item per stdout/stderr chunk
        data = '\n'.join(d['data'] if isinstance(d, dict) else str(d) for d in data
                         if not isinstance(d, dict) or isinstance(d.get('data'), str))
    text = data if isinstance(data, str) else json.dumps(data)[:5000] if data else ''
    return re.sub(r'\x1b\[[0-9;]*m', '', text).strip()


def write_notebook(model: dict, run, task_run_id: int):
    cells, md = [], ["# build_evidence: executed notebook with outputs", '',
                     f'Exported from serverless run [{run.run_id}]({run.run_page_url}) '
                     f'({ts(run.start_time)}, result **{run.state.result_state.value}**). '
                     'Re-create with `python evidence/collect_evidence.py`.', '']
    for i, cmd in enumerate(sorted(model['commands'], key=lambda c: c['position']), 1):
        src = cmd['command']
        if src.startswith('%md'):
            text = re.sub(r'^%md\s*\n?', '', src)
            cells.append({'cell_type': 'markdown', 'metadata': {}, 'source': text})
            md += [text, '']
            continue
        # pip's dependency-resolver chatter is noise; keep only whether the install ran
        output = '(pip install output omitted)' if src.startswith('%pip') else cell_text(cmd)
        cell = {'cell_type': 'code', 'execution_count': i, 'metadata': {}, 'source': src, 'outputs': []}
        if output:
            cell['outputs'].append({'output_type': 'stream', 'name': 'stdout', 'text': output + '\n'})
        cells.append(cell)
        lang = 'python' if not src.startswith('%') else ''
        md += [f'```{lang}', src, '```', '']
        if output:
            # Outputs are markdown tables or log lines; keep them verbatim
            md += ['**Output**', '', output if '|---' in output else f'```\n{output}\n```', '']
    nb = {'nbformat': 4, 'nbformat_minor': 5, 'cells': cells,
          'metadata': {'kernelspec': {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'},
                       'language_info': {'name': 'python'},
                       'databricks': {'run_id': run.run_id, 'task_run_id': task_run_id,
                                      'run_page_url': run.run_page_url}}}
    d = ROOT / 'notebook'
    d.mkdir(exist_ok=True)
    (d / 'build_evidence.ipynb').write_text(json.dumps(nb, indent=1) + '\n')
    (d / 'build_evidence.md').write_text('\n'.join(md) + '\n')


# ---------------------------------------------------------------- 3. deploy / app
def cli(*args, profile, cwd=None):
    p = subprocess.run(['databricks', *args, '--profile', profile], capture_output=True, text=True, cwd=cwd)
    return (p.stdout + p.stderr).strip()


def export_deploy(w: WorkspaceClient, profile: str):
    d = ROOT / 'deploy'
    d.mkdir(exist_ok=True)
    (d / 'bundle_summary.txt').write_text(
        '$ databricks bundle validate\n' + cli('bundle', 'validate', profile=profile, cwd=REPO / 'bundle')
        + '\n\n$ databricks bundle summary\n' + cli('bundle', 'summary', profile=profile, cwd=REPO / 'bundle') + '\n')
    app = w.apps.get(name=APP_NAME)
    deps = list(w.apps.list_deployments(app_name=APP_NAME))[:5]
    lines = [f'# Databricks App `{APP_NAME}`', '', f'- URL: {app.url}',
             f'- App status: **{app.app_status.state.value}** ({app.app_status.message})',
             f'- Compute: **{app.compute_status.state.value}**',
             f'- Service principal client id: `{app.service_principal_client_id}`',
             f'- User API scopes: {", ".join(app.user_api_scopes or [])}', '', '## Resources', '',
             '| Key | Type | Target | Permission |', '|---|---|---|---|']
    for r in app.resources:
        for kind, target in (('genie_space', 'space_id'), ('serving_endpoint', 'name'), ('job', 'id'),
                             ('postgres', 'branch'), ('sql_warehouse', 'id')):
            v = getattr(r, kind, None)
            if v:
                lines.append(f'| {r.name} | {kind} | `{getattr(v, target)}` | {v.permission.value} |')
    lines += ['', '## Recent deployments', '', '| Deployment | Status | Created |', '|---|---|---|']
    lines += [f'| `{x.deployment_id}` | {x.status.state.value} | {x.create_time} |' for x in deps]
    (d / 'app_status.md').write_text('\n'.join(lines) + '\n')
    logs = cli('apps', 'logs', APP_NAME, '--tail-lines', '300', profile=profile)
    logs = re.sub(r'\b\d{1,3}(\.\d{1,3}){3}\b', '<client-ip>', logs)  # don't commit client IPs
    (d / 'app_logs.txt').write_text(f'$ databricks apps logs {APP_NAME} --tail-lines 300\n'
                                    f'# captured {datetime.now(timezone.utc).isoformat(timespec="seconds")}\n'
                                    + logs + '\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', required=True)
    ap.add_argument('--job-run-id', type=int, required=True)
    ap.add_argument('--skip-notebook', action='store_true')
    args = ap.parse_args()
    w = WorkspaceClient(profile=args.profile)

    print('job run ->', export_job_run(w, args.job_run_id))
    if not args.skip_notebook:
        task_run_id, run = run_notebook(w)
        write_notebook(notebook_model(w, task_run_id), run, task_run_id)
        print('notebook ->', ROOT / 'notebook')
    export_deploy(w, args.profile)
    print('deploy ->', ROOT / 'deploy')


if __name__ == '__main__':
    main()
