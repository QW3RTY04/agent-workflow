"""Fail-quiet SessionStart reminder. Reads one metadata row and one small ledger."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys

try:
    from . import state
except ImportError:  # Direct hook command invocation.
    import state

MAX_INPUT = 16384
MAX_OUTPUT = 2048
ACTIVATION = state.ROOT / 'hook' / 'modules' / 'activation.md'


def read_input(stream):
    raw = stream.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (ValueError, TypeError):
        return None


def child_source(source):
    if isinstance(source, str):
        if 'subagent' in source.lower():
            return True
        try:
            source = json.loads(source)
        except ValueError:
            return False
    return isinstance(source, dict) and any(k in source for k in ('subagent', 'thread_spawn', 'parent_thread_id'))


def root_metadata(chat, database):
    # Do not glob/scan thread records or transcripts. Unknown schemas fail quiet.
    uri = Path(database).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=0.15)) as conn:
        conn.execute('PRAGMA query_only=ON')
        row = conn.execute('SELECT source FROM threads WHERE id = ? LIMIT 1', (chat,)).fetchone()
    return bool(row and row[0] and not child_source(row[0]))


def route(payload, database=None):
    if not isinstance(payload, dict):
        return {}
    try:
        chat = state.validate_id(payload.get('session_id'))
        event = payload.get('hook_event_name', 'SessionStart')
        lifecycle = payload.get('source', 'startup')
        if event != 'SessionStart' or lifecycle not in ('startup', 'resume', 'clear', 'compact', 'fork'):
            return {}
        for key in ('session_source', 'thread_source', 'provenance'):
            if child_source(payload.get(key)):
                return {}
        if payload.get('parent_thread_id') or payload.get('subagent') is not None:
            return {}
        if database is None:
            database = os.environ.get('WORKFLOW_HOOK_DATABASE', str(Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'state_5.sqlite'))
        if not root_metadata(chat, database):
            return {}
        record = state.load(chat)
        item = record['tasks'].get(record['current_task_id'])
        if lifecycle == 'fork':
            decision = 'Fork: inspect new scope; inherited consent is invalid. Use a new task ID.'
        else:
            decision = ('Offers suppressed in this chat.' if record['chat_suppressed'] else 'No chat suppression.')
            if item:
                decision += ' Task ' + item['task_id'] + ': ' + item['offer_state'] + '.'
            else:
                decision += ' No task decision.'
        context = (
            'Workflow routing for session ' + chat + ': ' + decision + ' Judge complexity during normal work. '
            'For substantial separable work, offer the agent workflow and live view once; skip simple work and decided continuations. '
            'Only a clear yes to a pending offer or an explicit workflow request authorizes activation. '
            'Record offer/accept/decline/revoke/suppress/enable with the sibling state.py helper (--help); reuse task IDs. '
            'After acceptance read ' + str(ACTIVATION) + '. '
            'Acceptance does not restart stopped work or override permissions, Stop/Pause, or project limits.'
        )
        output = dict(hookSpecificOutput=dict(hookEventName='SessionStart', additionalContext=context))
        return output if len(json.dumps(output).encode('utf-8')) <= MAX_OUTPUT else {}
    except (ValueError, OSError, sqlite3.Error):
        return {}


def main():
    # Never leak hook tracebacks into ordinary chats.
    try:
        print(json.dumps(route(read_input(sys.stdin))))
    except Exception:
        print('{}')


if __name__ == '__main__':
    main()
