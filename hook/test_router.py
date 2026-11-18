import io
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

try:
    from . import router, state
except ImportError:
    import router
    import state


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, WORKFLOW_HOOK_DATA_DIR=str(self.root / 'data'))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db = self.root / 'state.sqlite'
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, source TEXT)')
            conn.executemany('INSERT INTO threads VALUES (?, ?)', [
                ('root', 'vscode'), ('child', '{"subagent":{"thread_spawn":{"parent_thread_id":"root"}}}')])

    def route(self, **extra):
        return router.route(dict(session_id='root', source='startup', **extra), self.db)

    def test_accept_requires_pending_offer_and_duplicate_keeps_run(self):
        with self.assertRaises(ValueError):
            state.transition('root', 'task', 'accept')
        state.transition('root', 'task', 'offer', turn='turn1')
        first = state.transition('root', 'task', 'accept', turn='turn2')
        second = state.transition('root', 'task', 'accept')
        self.assertTrue(first['activation_claimed'])
        self.assertFalse(second['activation_claimed'])
        self.assertEqual(first['task']['run_id'], second['task']['run_id'])

    def test_decline_suppress_enable_and_new_task(self):
        state.transition('root', 'a', 'offer')
        state.transition('root', 'a', 'decline')
        self.assertEqual(state.transition('root', 'a', 'offer')['task']['offer_state'], 'declined')
        state.transition('root', 'b', 'suppress')
        with self.assertRaises(ValueError):
            state.transition('root', 'c', 'offer')
        state.transition('root', 'c', 'enable')
        self.assertEqual(state.transition('root', 'c', 'offer')['task']['offer_state'], 'offered')

    def test_resume_compact_restore_but_fork_does_not(self):
        state.transition('root', 'task', 'accept', explicit=True)
        for event in ('resume', 'compact'):
            output = router.route(dict(session_id='root', source=event), self.db)
            self.assertIn('accepted', json.dumps(output))
        self.assertNotIn('accepted', json.dumps(router.route(dict(session_id='root', source='fork'), self.db)))

    def test_child_and_unknown_provenance_emit_nothing(self):
        self.assertEqual(router.route(dict(session_id='child', source='startup'), self.db), {})
        self.assertEqual(self.route(session_source={'subagent': {}}), {})
        self.assertEqual(router.route(dict(session_id='unknown'), self.db), {})
        self.assertEqual(self.route(hook_event_name='SubagentStart'), {})
        self.assertEqual(router.route(dict(session_id='root'), self.root / 'missing.sqlite'), {})

    def test_corrupt_state_never_infers_acceptance(self):
        state.transition('root', 'task', 'offer')
        (state.data_root() / 'root.json').write_text('{broken', encoding='utf-8')
        self.assertEqual(self.route(), {})
        with self.assertRaises(ValueError):
            state.transition('root', 'task', 'accept', explicit=True)

    def test_ids_are_confined_and_output_bounded(self):
        with self.assertRaises(ValueError):
            state.transition('../escape', 'task', 'offer')
        output = self.route()
        context = output['hookSpecificOutput']['additionalContext']
        self.assertLessEqual(len(context.split()), 150)
        self.assertLessEqual(len(json.dumps(output).encode()), 2048)
        self.assertIn('activation.md', context)
        self.assertEqual(router.read_input(io.StringIO('x' * (router.MAX_INPUT + 1))), None)

    def test_parallel_accept_claims_activation_once(self):
        state.transition('root', 'task', 'offer')
        command = [sys.executable, str(Path(state.__file__)), 'accept', '--session', 'root', '--task', 'task']
        workers = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
        outputs = []
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=10)
            self.assertEqual(worker.returncode, 0, stderr)
            outputs.append(json.loads(stdout))
        self.assertEqual(sum(x['activation_claimed'] for x in outputs), 1)
        self.assertEqual(len({x['task']['run_id'] for x in outputs}), 1)

    def test_missing_schema_and_lifecycle_are_quiet(self):
        bad = self.root / 'empty.sqlite'
        with closing(sqlite3.connect(bad)):
            pass
        self.assertEqual(router.route(dict(session_id='root'), bad), {})
        self.assertEqual(router.route(dict(session_id='root', source='unexpected'), self.db), {})

    def test_task_storage_limit_preserves_previous_consent(self):
        state.transition('root', 'task', 'accept', explicit=True)
        with patch.object(state, 'MAX_TASKS', 1):
            with self.assertRaises(ValueError):
                state.transition('root', 'other', 'offer')
        self.assertEqual(state.get_state('root')['offer_state'], 'accepted')

    def test_revoked_acceptance_needs_explicit_new_request(self):
        accepted = state.transition('root', 'task', 'accept', explicit=True)
        revoked = state.transition('root', 'task', 'revoke')
        self.assertEqual(revoked['task']['offer_state'], 'revoked')
        self.assertEqual(state.get_state('root')['offer_state'], 'revoked')
        self.assertEqual(state.transition('root', 'task', 'offer')['task']['offer_state'], 'revoked')
        with self.assertRaises(ValueError):
            state.transition('root', 'task', 'accept')
        renewed = state.transition('root', 'task', 'accept', explicit=True)
        self.assertTrue(renewed['activation_claimed'])
        self.assertNotEqual(accepted['task']['run_id'], renewed['task']['run_id'])

    def test_decline_can_revoke_accepted_task(self):
        state.transition('root', 'task', 'accept', explicit=True)
        self.assertEqual(state.transition('root', 'task', 'decline')['task']['offer_state'], 'revoked')

    def test_suppression_is_restored_for_accepted_task(self):
        state.transition('root', 'task', 'accept', explicit=True)
        state.transition('root', 'task', 'suppress')
        self.assertTrue(state.get_state('root')['chat_suppressed'])
        self.assertIn('Offers suppressed', json.dumps(self.route()))


if __name__ == '__main__':
    unittest.main()
