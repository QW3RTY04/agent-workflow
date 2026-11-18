import json
from pathlib import Path
import tempfile
import unittest
from monitor import Agent, Monitor


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'events.jsonl'
        self.path.touch()
        self.agent = Agent(dict(id='test', rollout_path=str(self.path)), 'match')

    def event(self, kind, payload):
        return dict(timestamp='2026-11-14T12:00:00Z', type=kind, payload=payload)

    def test_visible_summary_only(self):
        self.agent.consume(self.event('response_item', dict(type='reasoning', summary=[dict(type='summary_text', text='Matching verified sources')], content=[dict(text='PRIVATE_REASONING')])), {'match'})
        self.agent.consume(self.event('response_item', dict(type='message', role='assistant', channel='analysis', content=[dict(type='output_text', text='PRIVATE_ANALYSIS')])), {'match'})
        self.agent.consume(self.event('response_item', dict(type='function_call', name='web_search', arguments='SECRET_ARGUMENTS')), {'match'})
        output = json.dumps(self.agent.data)
        self.assertIn('Matching verified sources', output)
        for value in ('PRIVATE_REASONING', 'PRIVATE_ANALYSIS', 'SECRET_ARGUMENTS'):
            self.assertNotIn(value, output)

    def test_explicit_progress_and_stop(self):
        self.agent.consume(self.event('event_msg', dict(type='task_started')), {'match'})
        self.assertEqual(self.agent.data['status'], 'running')
        text = '[workflow:match] {"current":"Matching verified evidence","next":"Choose formats","status":"running"}'
        self.agent.consume(self.event('response_item', dict(type='message', role='assistant', channel='commentary', content=[dict(type='output_text', text=text)])), {'match'})
        self.assertEqual(self.agent.data['next'], 'Choose formats')
        self.assertEqual(self.agent.data['mapping'], 'Agent-reported step')
        self.agent.consume(self.event('event_msg', dict(type='turn_aborted')), {'match'})
        self.assertEqual(self.agent.data['status'], 'stopped')
        self.agent.consume(self.event('event_msg', dict(type='task_started')), {'match'})
        self.assertEqual(self.agent.data['next'], '')
        self.agent.consume(self.event('event_msg', dict(type='task_complete')), {'match'})
        self.assertEqual(self.agent.data['status'], 'completed')

    def test_incremental_partial_records(self):
        record = json.dumps(self.event('event_msg', dict(type='task_started'))).encode()
        self.path.write_bytes(record[:25])
        self.agent.refresh({'match'})
        self.assertEqual(self.agent.offset, 0)
        with self.path.open('ab') as f:
            f.write(record[25:] + b'\n')
        self.agent.refresh({'match'})
        self.assertEqual(self.agent.data['status'], 'running')
        offset = self.agent.offset
        self.agent.refresh({'match'})
        self.assertEqual(self.agent.offset, offset)
        self.assertEqual(self.agent.data['activity'], [])

    def test_effort_from_actual_turn(self):
        self.agent.consume(self.event('turn_context', dict(model='test-model', collaboration_mode=dict(settings=dict(reasoning_effort='low')))), {'match'})
        self.assertEqual((self.agent.data['model'], self.agent.data['effort']), ('test-model', 'low'))

    def test_step_history_does_not_reuse_previous_thinking(self):
        def progress(node):
            text = '[workflow:' + node + '] {"status":"running","current":"Test","next":"Review"}'
            self.agent.consume(self.event('response_item', dict(type='message', role='assistant', channel='commentary', content=[dict(type='output_text', text=text)])), {'match', 'review'})
        progress('match')
        self.agent.consume(self.event('response_item', dict(type='reasoning', summary=[dict(type='summary_text', text='Matching evidence')])), {'match', 'review'})
        progress('review')
        self.assertEqual(self.agent.data['steps']['match']['summary'], 'Matching evidence')
        self.assertEqual(self.agent.data['steps']['match']['status'], 'visited')
        self.assertEqual(self.agent.data['steps']['review']['summary'], '')

    def test_descendant_scope(self):
        import sqlite3
        database = Path(self.temp.name) / 'state.sqlite'
        columns = 'id, rollout_path, source, title, agent_nickname, agent_path, model, reasoning_effort, agent_role'
        with sqlite3.connect(database) as c:
            c.execute('CREATE TABLE threads (' + ','.join(x.strip()+' TEXT' for x in columns.split(',')) + ')')
            for ident, parent in [('root', None), ('child', 'root'), ('grandchild', 'child'), ('unrelated', 'other')]:
                source = json.dumps(dict(subagent=dict(thread_spawn=dict(parent_thread_id=parent)))) if parent else 'vscode'
                c.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?)', (ident,str(self.path),source,ident,None,None,'model','low',None))
        c.close()
        workflow = Path(self.temp.name) / 'flow.json'
        workflow.write_text('{"nodes":[{"id":"match"}]}')
        monitor = Monitor(dict(database=str(database), workflow=str(workflow), thread='root', title='Test'))
        data = json.loads(monitor.snapshot())
        self.assertEqual({a['id'] for a in data['agents']}, {'root', 'child', 'grandchild'})


if __name__ == '__main__':
    unittest.main()
