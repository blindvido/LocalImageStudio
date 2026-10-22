import tempfile
import unittest
from pathlib import Path
from agent_cli import read_request, choose_workflow, compact_job
import contextlib
import io
from unittest.mock import patch
import agent_cli


class CliTests(unittest.TestCase):
    def test_panel_commands_and_structured_generate_do_not_require_workflow(self):
        calls = []
        class API:
            def __init__(self, url): pass
            def connect(self): pass
            def request(self, path, method='GET', body=None):
                calls.append((path, method, body))
                if path == 'jobs': return {'id': 'job', 'state': 'done', 'workflow': {'name': 'NAI'}, 'images': []}
                return {'id': 'panel'}
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / 'panel.json'; request.write_text('{"panel_id":"p","request_id":"r"}')
            with patch.object(agent_cli, 'LocalAPI', API), contextlib.redirect_stdout(io.StringIO()):
                for argv in [['panel-list'], ['panel-get', 'p'], ['panel-save', '--request-file', str(request)], ['generate', '--request-file', str(request)]]:
                    try: result = agent_cli.main(argv)
                    except SystemExit: self.fail('Panel CLI commands must exist')
                    self.assertEqual(result, 0)
        self.assertEqual([x[:2] for x in calls], [('panels', 'GET'), ('panels/p', 'GET'), ('panels', 'POST'), ('jobs', 'POST')])
        self.assertFalse(calls[-1][2]['use_api'])
    def test_agent_can_inspect_edit_session_for_recovery(self):
        calls=[]
        class API:
            def __init__(self,url):pass
            def connect(self):pass
            def request(self,path):calls.append(path);return {'id':'edit-id','latest_job_id':'job-id'}
        with patch.object(agent_cli,'LocalAPI',API),contextlib.redirect_stdout(io.StringIO()):
            try: result=agent_cli.main(['edit-session','edit-id'])
            except SystemExit:self.fail('Agent CLI must support edit-session')
            self.assertEqual(result,0)
        self.assertEqual(calls,['edit/edit-id'])
    def test_agent_edit_upload_sends_image_content_without_server_path(self):
        calls = []
        class API:
            def __init__(self, url): pass
            def connect(self): pass
            def request(self, path, method='GET', body=None):
                calls.append((path, method, body)); return {'id':'edit-id'}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.png'; path.write_bytes(b'png-bytes')
            with patch.object(agent_cli, 'LocalAPI', API), contextlib.redirect_stdout(io.StringIO()):
                try: result = agent_cli.main(['edit-upload', '--image', str(path)])
                except SystemExit: self.fail('Agent CLI must support edit-upload')
                self.assertEqual(result, 0)
        self.assertEqual(calls[0][:2], ('edit/upload', 'POST'))
        import base64
        self.assertEqual(base64.b64decode(calls[0][2]['image_base64']), b'png-bytes')

    def test_request_file_preserves_chinese_and_quotes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'request.json'
            path.write_text('{"description":"窗前女孩，招牌写着 \\"hello\\""}', encoding='utf-8-sig')
            self.assertEqual(read_request(path)['description'], '窗前女孩，招牌写着 "hello"')

    def test_workflow_name_must_be_unambiguous(self):
        workflows = [{'id': 'a', 'name': 'same'}, {'id': 'b', 'name': 'same'}]
        self.assertEqual(choose_workflow(workflows, 'a'), 'a')
        with self.assertRaisesRegex(ValueError, '多个'):
            choose_workflow(workflows, 'same')
        with self.assertRaisesRegex(ValueError, '选择'):
            choose_workflow(workflows, None)

    def test_compact_job_retains_prompt_id_and_outputs(self):
        result = compact_job({'id': 'a', 'prompt_id': 'p', 'state': 'done', 'workflow': {'graph': {'secret': 'irrelevant'}, 'name': 'test'}, 'images': [{'filename': 'a.png'}], 'values': {'seed': 42}})
        self.assertEqual(result['prompt_id'], 'p')
        self.assertEqual(result['values']['seed'], 42)
        self.assertNotIn('graph', str(result))
