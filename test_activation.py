import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import workflow_control as control
from hook import state
from module_registry import resolve


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, WORKFLOW_HOOK_DATA_DIR=str(self.root / 'state'))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.spec = self.root / 'spec.json'
        self.spec.write_text(json.dumps(dict(title='Test', nodes=[dict(id='read-1', label='Read files')], edges=[])))
        self.runs = patch.object(control, 'RUNS', self.root / 'runs')
        self.runs.start()
        self.addCleanup(self.runs.stop)
        self.row = dict(id='session', model='gpt-6.1-sol', reasoning_effort='medium', source='vscode')
        self.meta = patch.object(control, 'thread_row', return_value=(self.row, self.root / 'db.sqlite'))
        self.meta.start()
        self.addCleanup(self.meta.stop)

    def accept(self, task='task'):
        return state.transition('session', task, 'accept', explicit=True)

    def test_acceptance_creates_one_isolated_run(self):
        self.accept()
        first = control.create_run('session', 'task', self.spec)
        self.assertEqual(first, control.create_run('session', 'task', self.spec))
        self.accept('other')
        second = control.create_run('session', 'other', self.spec)
        self.assertNotEqual(first, second)
        self.assertEqual(first, control.create_run('session', 'task', self.spec))
        self.assertEqual(len(list((self.root / 'runs').glob('*/config.json'))), 2)

    def test_decline_revoke_and_suppression_block_creation(self):
        with self.assertRaises(ValueError):
            control.create_run('session', 'task', self.spec)
        self.accept()
        state.transition('session', 'task', 'revoke')
        with self.assertRaises(ValueError):
            control.create_run('session', 'task', self.spec)
        self.accept()
        state.transition('session', 'task', 'suppress')
        with self.assertRaises(ValueError):
            control.create_run('session', 'task', self.spec)

    def test_wrong_model_or_unrelated_supervisor_cannot_be_relabelled(self):
        self.accept()
        self.row['model'] = 'gpt-6-luna'
        with self.assertRaises(ValueError):
            control.create_run('session', 'task', self.spec)
        self.row['model'] = 'gpt-6.1-sol'
        self.row['source'] = json.dumps({'subagent': {'thread_spawn': {'parent_thread_id': 'other'}}})
        with self.assertRaises(ValueError):
            control.create_run('session', 'task', self.spec, 'child')

    def test_module_graph_is_selective_and_rejects_escape_and_cycles(self):
        ids = [x['id'] for x in resolve(['plan', 'progress', 'plan'])]
        self.assertEqual(ids, ['plan', 'progress'])
        with self.assertRaises(ValueError):
            resolve(['bad'], {'bad': {'path': '../outside.md'}})
        with self.assertRaises(ValueError):
            resolve(['a'], {'a': {'path': 'README.md', 'requires': ['b']}, 'b': {'path': 'README.md', 'requires': ['a']}})


if __name__ == '__main__':
    unittest.main()
