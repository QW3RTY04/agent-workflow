"""Local, read-only Codex activity monitor. Python standard library only."""
from collections import deque
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import threading
import time

ROOT = Path(__file__).resolve().parent
MARKER = re.compile(r'^\[workflow:([a-z][a-z0-9_-]{0,63})\]\s*(\{[^\n]+\})\s*$', re.M)


def clean_path(value):
    # Older desktop records can contain doubled Windows path separators.
    if value.startswith('\\\\\\\\?\\\\'):
        value = value[7:]
    if value.startswith('\\\\?\\'):
        value = value[4:]
    return Path(re.sub(r'\\+', r'\\', value))


class Agent:
    def __init__(self, row, node=None):
        self.path = clean_path(row['rollout_path'])
        self.offset = 0
        self.assigned_node = node
        self.data = dict(id=row['id'], name=row.get('agent_nickname') or row.get('agent_path') or row.get('title') or 'Coordinator',
                         model=row.get('model') or 'Unavailable', effort=row.get('reasoning_effort') or 'Unavailable',
                         node=node, mapping='Configured assignment' if node else 'Unassigned', status='idle',
                         current='', next='', nextSource='Not reported', summary='', updated='', activity=[], steps={})
        self.history = deque(maxlen=12)

    def consume(self, record, valid_nodes):
        p = record.get('payload', {})
        kind, event = record.get('type'), p.get('type')
        stamp = record.get('timestamp', '')
        if kind == 'turn_context':
            self.data['model'] = p.get('model') or self.data['model']
            self.data['effort'] = p.get('effort') or p.get('reasoning_effort') or p.get('collaboration_mode', {}).get('settings', {}).get('reasoning_effort') or self.data['effort']
        if kind == 'event_msg':
            status = {'task_started': 'running', 'task_complete': 'completed', 'turn_aborted': 'stopped'}
            if event in status:
                self.data['status'] = self.data['status'] if event == 'task_complete' and self.data['status'] in ('failed', 'waiting') else status[event]
                self.data['updated'] = stamp
                if event == 'task_started':
                    self.data.update(node=self.assigned_node, current='', next='', nextSource='Not reported', summary='')
            # Other records can contain prompts or private content: do not forward them.
        if kind == 'response_item' and event == 'reasoning':
            text = '\n'.join(x.get('text', '') for x in p.get('summary', []) if x.get('type') == 'summary_text')
            if text:
                self.data['summary'] = text[-4000:]
                self.history.append(dict(kind='Thinking summary', text=text[-1500:], at=stamp))
                self.data['updated'] = stamp
        if kind == 'response_item' and event in ('message', 'agent_message'):
            if p.get('role', 'assistant' if event == 'agent_message' else '') != 'assistant':
                return
            # Never expose analysis-channel content.
            if p.get('channel') not in (None, 'commentary', 'final'):
                return
            content = p.get('content', [])
            text = '\n'.join(x.get('text', '') for x in content if isinstance(x, dict) and x.get('type') in ('output_text', 'text')) if isinstance(content, list) else ''
            text = text or p.get('text', '')
            if text:
                self.data['current'] = text[-5000:]
                self.data['updated'] = stamp
                self.history.append(dict(kind='Agent update', text=text[-1500:], at=stamp))
            for match in MARKER.finditer(text):
                if match[1] not in valid_nodes:
                    continue
                try:
                    report = json.loads(match[2])
                except ValueError:
                    continue
                previous = self.data['node']
                if previous != match[1]:
                    if previous in self.data['steps'] and self.data['steps'][previous]['status'] == 'running':
                        self.data['steps'][previous]['status'] = 'visited'
                    self.data['summary'] = ''
                self.data.update(node=match[1], mapping='Agent-reported step', next=str(report.get('next', ''))[:1000], nextSource='Agent-reported intention')
                if isinstance(report.get('current'), str):
                    self.data['current'] = report['current'][:2000]
                if report.get('status') in ('running', 'waiting', 'completed', 'failed', 'stopped'):
                    self.data['status'] = report['status']
        if kind == 'response_item' and event in ('function_call', 'custom_tool_call'):
            # Names only. Tool arguments/output may contain secrets or internal code.
            name = str(p.get('name', 'Tool'))[:150]
            self.history.append(dict(kind='Tool call', text=name, at=stamp))
            self.data['updated'] = stamp
        self.data['activity'] = list(self.history)
        if self.data['node']:
            self.data['steps'][self.data['node']] = {k: self.data[k] for k in ('status', 'current', 'next', 'nextSource', 'summary', 'updated', 'model', 'effort', 'mapping')}

    def refresh(self, valid_nodes):
        try:
            if self.path.stat().st_size < self.offset:
                self.offset = 0
            with self.path.open('rb') as stream:
                stream.seek(self.offset)
                while True:
                    start = stream.tell()
                    line = stream.readline()
                    if not line or not line.endswith(b'\n'):
                        self.offset = start
                        break
                    self.offset = stream.tell()
                    try:
                        self.consume(json.loads(line), valid_nodes)
                    except (ValueError, TypeError, AttributeError):
                        continue
            self.data.pop('error', None)
        except OSError as exc:
            self.data['error'] = str(exc)


class Monitor:
    def __init__(self, config, config_path=None):
        self.config = config
        self.config_path = config_path
        self.agents = {}
        self.last_discovery = 0
        self.lock = threading.Lock()
        self.error = None
        self.nodes = json.loads(Path(config['workflow']).read_text(encoding='utf-8'))['nodes']
        self.valid_nodes = {x['id'] for x in self.nodes}

    def discover(self):
        db = Path(self.config['database']).as_uri() + '?mode=ro'
        with closing(sqlite3.connect(db, uri=True, timeout=2)) as conn:
            conn.row_factory = sqlite3.Row
            # Only root metadata and subagent metadata; no messages or credentials.
            rows = [dict(x) for x in conn.execute('SELECT id, rollout_path, source, title, agent_nickname, agent_role, agent_path, model, reasoning_effort FROM threads WHERE id = ? OR source LIKE ?', (self.config['thread'], '%thread_spawn%'))]
        parents = {}
        for row in rows:
            try:
                parents[row['id']] = json.loads(row['source']).get('subagent', {}).get('thread_spawn', {}).get('parent_thread_id')
            except (ValueError, TypeError):
                pass
        allowed = {self.config['thread']}
        while True:
            children = {k for k, v in parents.items() if v in allowed}
            if children <= allowed:
                break
            allowed |= children
        for row in rows:
            if row['id'] in allowed and row['id'] not in self.agents:
                self.agents[row['id']] = Agent(row, self.config.get('bindings', {}).get(row['id']))
                role = row.get('agent_role') or (row.get('agent_path') or '').split('/')[-1]
                names = {x['role']: x['name'] for x in self.config.get('routing', [])}
                self.agents[row['id']].data['name'] = self.config.get('agent_names', {}).get(row['id']) or names.get(role) or self.agents[row['id']].data['name']

    def snapshot(self):
        with self.lock:
            try:
                if self.config_path:
                    fresh = json.loads(self.config_path.read_text(encoding='utf-8'))
                    if fresh != self.config:
                        self.config = fresh
                        self.agents = {}
                        self.last_discovery = 0
                        self.nodes = json.loads(Path(fresh['workflow']).read_text(encoding='utf-8'))['nodes']
                        self.valid_nodes = {x['id'] for x in self.nodes}
                if time.monotonic() - self.last_discovery > 8:
                    self.discover()
                    self.last_discovery = time.monotonic()
                for agent in self.agents.values():
                    agent.refresh(self.valid_nodes)
                self.error = None
            except (sqlite3.Error, OSError) as exc:
                self.error = str(exc)
            # Serialize under lock: request handlers must never share mutable state.
            workflow = json.loads(Path(self.config['workflow']).read_text(encoding='utf-8'))
            return json.dumps(dict(thread=self.config['thread'], title=self.config['title'], revision=self.config.get('revision', 1), routing=self.config.get('routing', []), agents=[x.data for x in self.agents.values()], nodes=self.nodes, edges=workflow.get('edges', []), error=self.error), ensure_ascii=False).encode('utf-8')
