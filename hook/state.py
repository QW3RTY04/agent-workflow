"""Small consent ledger. No prompt text; no model calls. See --help for the CLI."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
MAX_STATE = 65536
MAX_TASKS = 64


def data_root():
    return Path(os.environ.get('WORKFLOW_HOOK_DATA_DIR', ROOT / 'hook-data')).resolve()


def validate_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', value):
        raise ValueError('IDs must be 1–96 letters, digits, underscores or hyphens')
    if value.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}:
        raise ValueError('reserved Windows filename ID')
    return value


def load(chat):
    validate_id(chat)
    path = data_root() / (chat + '.json')
    if not path.exists():
        return dict(schema_version=1, policy_version=1, root_chat_id=chat,
                    chat_suppressed=False, current_task_id=None, tasks={})
    try:
        if path.stat().st_size > MAX_STATE:
            raise ValueError('oversized state')
        record = json.loads(path.read_text(encoding='utf-8'))
        if (record['schema_version'] != 1 or record['policy_version'] != 1
                or record['root_chat_id'] != chat
                or not isinstance(record['chat_suppressed'], bool)
                or not isinstance(record['tasks'], dict) or len(record['tasks']) > MAX_TASKS):
            raise ValueError('unsupported state')
        for task, item in record['tasks'].items():
            validate_id(task)
            if item['task_id'] != task or item['offer_state'] not in ('offered', 'accepted', 'declined', 'revoked'):
                raise ValueError('invalid task')
            for field in ('offer_turn_id', 'decision_turn_id'):
                if item.get(field) is not None:
                    validate_id(item[field])
            if item['offer_state'] == 'accepted':
                validate_id(item['run_id'])
        if record['current_task_id'] is not None:
            validate_id(record['current_task_id'])
            if record['current_task_id'] not in record['tasks']:
                raise ValueError('missing current task')
        return record
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid decision state; manual recovery required') from exc


@contextmanager
def locked(chat):
    folder = data_root()
    folder.mkdir(parents=True, exist_ok=True)
    handle = open(folder / (validate_id(chat) + '.lock'), 'a+b')
    try:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        deadline = time.monotonic() + 0.75
        while True:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError('decision state busy; retry later')
                time.sleep(0.02)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def save(chat, record):
    encoded = json.dumps(record, separators=(',', ':')).encode('utf-8')
    if len(encoded) > MAX_STATE:
        raise ValueError('decision state full')
    filename = None
    try:
        with tempfile.NamedTemporaryFile(dir=data_root(), prefix=chat + '.', suffix='.tmp', delete=False) as stream:
            filename = stream.name
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, data_root() / (chat + '.json'))
    finally:
        if filename and os.path.exists(filename):
            os.unlink(filename)


def get_state(chat):
    """Return current task fields plus chat suppression; never infer consent."""
    record = load(chat)
    item = record['tasks'].get(record['current_task_id'])
    return dict(item or {}, chat_suppressed=record['chat_suppressed'])


def transition(chat, task, action, *, turn=None, explicit=False):
    validate_id(chat)
    validate_id(task)
    if turn is not None:
        validate_id(turn)
    if action not in ('read', 'offer', 'accept', 'decline', 'revoke', 'suppress', 'enable'):
        raise ValueError('unknown decision action')
    with locked(chat):
        record = load(chat)
        item = record['tasks'].get(task)
        claimed = False
        if action == 'read':
            return dict(chat_suppressed=record['chat_suppressed'], task=item, activation_claimed=False)
        if action in ('suppress', 'enable'):
            record['chat_suppressed'] = action == 'suppress'
        elif action == 'offer':
            if record['chat_suppressed']:
                raise ValueError('offers suppressed in this chat')
            if item is None:
                item = dict(task_id=task, offer_state='offered', offer_turn_id=turn, decision_turn_id=None)
        elif action == 'revoke' or (action == 'decline' and item and item['offer_state'] == 'accepted'):
            if not item or item['offer_state'] not in ('accepted', 'revoked'):
                raise ValueError('revocation requires an accepted task')
            item['offer_state'] = 'revoked'
            item['decision_turn_id'] = turn
        elif action in ('accept', 'decline'):
            if item and item['offer_state'] == ('accepted' if action == 'accept' else 'declined'):
                pass  # Retries cannot allocate another run or claim activation.
            elif action == 'accept' and explicit:
                item = dict(task_id=task, offer_state='accepted', offer_turn_id=None,
                            decision_turn_id=turn, run_id=uuid.uuid4().hex)
                claimed = True
            elif not item or item['offer_state'] != 'offered':
                raise ValueError('decision requires a pending offer; direct requests use --explicit')
            else:
                item['offer_state'] = 'accepted' if action == 'accept' else 'declined'
                item['decision_turn_id'] = turn
                if action == 'accept':
                    item['run_id'] = uuid.uuid4().hex
                    claimed = True
        if item is not None:
            if task not in record['tasks'] and len(record['tasks']) >= MAX_TASKS:
                raise ValueError('task limit reached; archive state explicitly')
            record['tasks'][task] = item
            record['current_task_id'] = task
        record['updated_at'] = datetime.now(timezone.utc).isoformat()
        save(chat, record)
        return dict(chat_suppressed=record['chat_suppressed'], task=item, activation_claimed=claimed)


def main():
    parser = argparse.ArgumentParser(description='Save task consent. Run actions only for a clear user decision. Duplicate acceptance never claims activation twice.')
    parser.add_argument('action', choices=('read', 'offer', 'accept', 'decline', 'revoke', 'suppress', 'enable'))
    parser.add_argument('--chat', '--session', dest='chat', required=True, help='Exact root session ID')
    parser.add_argument('--task', required=True, help='Stable objective ID; reuse for continuations')
    parser.add_argument('--turn', help='Offer or decision turn ID')
    parser.add_argument('--explicit', action='store_true', help='Accept a direct workflow request without a pending offer')
    args = parser.parse_args()
    try:
        print(json.dumps(transition(args.chat, args.task, args.action, turn=args.turn, explicit=args.explicit)))
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
