"""Create an accepted task's diagram, start its local service, and open its view."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
import webbrowser
from contextlib import closing
from monitor import ROOT

RUNS = ROOT / 'runs'


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.pending')
    temp.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def thread_row(session):
    db = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'state_5.sqlite'
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute('SELECT id,model,reasoning_effort,source FROM threads WHERE id=?', (session,)).fetchone()
    if not row:
        raise ValueError('The selected chat is not available in local Codex records.')
    return dict(row), db


def create_run(session, task, spec_path, supervisor=None):
    from task_diagram import validate, render
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', task):
        raise ValueError('Invalid session or task ID')
    from hook.state import load, locked
    record = load(session)
    consent = record['tasks'].get(task, {})
    # State is advisory routing, not a substitute for native user authorization.
    if record['chat_suppressed'] or consent.get('task_id') != task or consent.get('offer_state') != 'accepted':
        raise ValueError('Record an affirmative workflow decision for this task first.')
    row, database = thread_row(supervisor or session)
    if supervisor and supervisor != session:
        source = json.loads(row['source'])
        parent = source.get('subagent', {}).get('thread_spawn', {}).get('parent_thread_id')
        if parent != session:
            raise ValueError('Pinned supervisor must be a direct child of the requesting root chat.')
    if (row['model'], row['reasoning_effort']) != ('gpt-6.1-sol', 'medium'):
        raise ValueError('Supervisor must be GPT-6.1 Sol / medium. Use a pinned supervisor agent; do not relabel this chat.')
    spec = json.loads(Path(spec_path).read_text(encoding='utf-8'))
    validate(spec)
    run_id = consent['run_id'][:24]
    folder = RUNS / run_id
    folder.mkdir(parents=True, exist_ok=True)
    # One creation per accepted task; retries reuse its live URL and work definition.
    with locked(session):
        fresh = load(session)
        if fresh['chat_suppressed'] or fresh['tasks'].get(task, {}).get('offer_state') != 'accepted' or fresh['tasks'][task].get('run_id') != consent.get('run_id'):
            raise ValueError('The workflow decision changed before activation.')
        if not (folder / 'config.json').exists():
            atomic(folder / 'workflow.json', spec)
            (folder / 'diagram.html').write_text(render(spec), encoding='utf-8')
            base = json.loads((ROOT / 'config.example.json').read_text(encoding='utf-8'))
            config = dict(title=spec['title'], thread=row['id'], request_chat=session, task_id=task, database=str(database), workflow=str(folder / 'workflow.json'), diagram=str(folder / 'diagram.html'), bindings={}, agent_names={row['id']: 'Plan work'}, routing=base['routing'], revision=1)
            atomic(folder / 'config.json', config)
    return run_id


def start_service():
    def alive():
        try:
            with urllib.request.urlopen('http://127.0.0.1:8768/health', timeout=1) as response:
                return json.load(response).get('service') == 'agent-workflow'
        except (OSError, ValueError):
            return False
    if alive():
        return
    log = (ROOT / 'workflow-service.log').open('ab')
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {'start_new_session': True}
    with log:
        subprocess.Popen([sys.executable, str(ROOT / 'workflow_service.py')], cwd=ROOT, stdout=log, stderr=log, stdin=subprocess.DEVNULL, **options)
    for _ in range(30):
        if alive():
            return
        time.sleep(.1)
    raise RuntimeError('Live view service did not become available; inspect workflow-service.log.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('create')
    create.add_argument('--session', required=True)
    create.add_argument('--task', required=True)
    create.add_argument('--spec', type=Path, required=True)
    create.add_argument('--supervisor')
    create.add_argument('--open', action='store_true')
    assign = sub.add_parser('assign')
    assign.add_argument('--run', required=True)
    assign.add_argument('--agent', required=True)
    assign.add_argument('--name', required=True)
    assign.add_argument('--node', required=True)
    update = sub.add_parser('update')
    update.add_argument('--run', required=True)
    update.add_argument('--spec', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == 'create':
            run_id = create_run(args.session, args.task, args.spec, args.supervisor)
            start_service()
            url = 'http://127.0.0.1:8768/runs/' + run_id
            if args.open:
                webbrowser.open(url)
            print(json.dumps(dict(run_id=run_id, url=url)))
        elif args.action == 'update':
            from hook.state import locked, load
            from task_diagram import validate, render
            if not re.fullmatch(r'[a-f0-9]{24}', args.run):
                raise ValueError('Invalid run ID')
            folder = RUNS / args.run
            config = json.loads((folder / 'config.json').read_text(encoding='utf-8'))
            consent = load(config['request_chat'])
            if consent['chat_suppressed'] or consent['tasks'].get(config.get('task_id'), {}).get('offer_state') != 'accepted':
                raise ValueError('This run no longer has accepted consent.')
            spec = json.loads(args.spec.read_text(encoding='utf-8'))
            validate(spec)
            with locked('run-' + args.run):
                atomic(folder / 'workflow.json', spec)
                html_path = folder / 'diagram.html'
                temporary = folder / 'diagram.pending'
                temporary.write_text(render(spec), encoding='utf-8')
                temporary.replace(html_path)
                config['title'] = spec['title']
                config['revision'] = config.get('revision', 1) + 1
                atomic(folder / 'config.json', config)
            print(json.dumps(dict(updated=True, revision=config['revision'])))
        else:
            if not re.fullmatch(r'[a-f0-9]{24}', args.run):
                raise ValueError('Invalid run ID')
            path = RUNS / args.run / 'config.json'
            config = json.loads(path.read_text(encoding='utf-8'))
            workflow = json.loads(Path(config['workflow']).read_text(encoding='utf-8'))
            if args.node not in {n['id'] for n in workflow['nodes']}:
                raise ValueError('Unknown node')
            from hook.state import locked
            with locked('run-' + args.run):
                config = json.loads(path.read_text(encoding='utf-8'))
                config['agent_names'][args.agent] = args.name[:80]
                config['bindings'][args.agent] = args.node
                atomic(path, config)
            print('{"assigned":true}')
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, str(exc) + '\n')
