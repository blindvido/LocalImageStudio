import tempfile
import unittest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from studio_server import create_app


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.mode = 'reject'
        async def fake_submit(request):
            self.submitted_graph = (await request.json())['prompt']
            if self.mode == 'reject':
                return web.json_response({'error': {'message': 'missing model'}}, status=400)
            if self.mode == 'unconfirmed':
                return web.json_response({})
            return web.json_response({'prompt_id': 'mock-id', 'node_errors': {}})
        async def fake_history(request):
            if self.mode == 'offline':
                return web.Response(status=503, text='offline')
            return web.json_response({'mock-id': {'status': {'completed': True, 'status_str': 'success'}, 'outputs': {'7': {'images': [{'filename': 'image.png', 'subfolder': '', 'type': 'output'}]}}}})
        async def fake_completion(request):
            self.prompt_request = await request.json()
            return web.json_response({'choices': [{'message': {'content': '{"positive_prompt":"watercolor lighthouse", "negative_prompt":"blurry"}'}}]})
        fake = web.Application()
        fake.router.add_post('/prompt', fake_submit)
        fake.router.add_get('/history/{id}', fake_history)
        fake.router.add_post('/v1/chat/completions', fake_completion)
        self.comfy = TestServer(fake)
        await self.comfy.start_server()
        self.client = TestClient(TestServer(create_app(self.directory.name)))
        await self.client.start_server()
        self.token = (await (await self.client.get('/api/bootstrap')).json())['token']
        self.headers = {'X-Studio-Token': self.token}
        await self.client.put('/api/config', json={'comfy_url': str(self.comfy.make_url('')).rstrip('/')}, headers=self.headers)

    async def asyncTearDown(self):
        await self.client.close()
        await self.comfy.close()
        self.directory.cleanup()

    async def test_config_key_redacted_and_persisted(self):
        response = await self.client.put('/api/config', json={'api_key': 'very-secret', 'model': 'demo'}, headers=self.headers)
        result = await response.json()
        self.assertEqual(response.status, 200)
        self.assertTrue(result['has_api_key'])
        self.assertNotIn('very-secret', str(result))
        await self.client.put('/api/config', json={'model': 'other'}, headers=self.headers)
        from storage import Store
        self.assertEqual(Store(self.directory.name).config['api_key'], 'very-secret')

    async def test_token_and_cross_origin_protection(self):
        self.assertEqual((await self.client.put('/api/config', json={})).status, 403)
        self.assertEqual((await self.client.get('/api/bootstrap', headers={'Origin': 'https://evil.example'})).status, 403)
        self.assertEqual((await self.client.put('/api/config', json={}, headers={**self.headers, 'Origin': 'https://evil.example'})).status, 403)

    async def test_import_mapping_and_original_not_mutated(self):
        graph = {'1': {'class_type': 'CLIPTextEncode', 'inputs': {'text': '%prompt%'}}}
        response = await self.client.post('/api/workflows', json={'name': 'test workflow', 'graph': graph}, headers=self.headers)
        self.assertEqual(response.status, 200)
        workflow = await response.json()
        self.assertEqual(workflow['inspection']['variables'], ['prompt'])
        response = await self.client.put('/api/workflows/' + workflow['id'], json={'name': 'new name', 'mappings': {'prompt': ['99', 'text']}}, headers=self.headers)
        self.assertEqual(response.status, 400)
        workflows = await (await self.client.get('/api/workflows')).json()
        self.assertEqual(next(w for w in workflows if w['id'] == workflow['id'])['name'], 'test workflow')

    async def test_invalid_graph_import(self):
        response = await self.client.post('/api/workflows', json={'name': 'bad', 'graph': {'nodes': []}}, headers=self.headers)
        self.assertEqual(response.status, 400)

    async def test_job_failure_retains_input(self):
        workflow = (await (await self.client.get('/api/workflows')).json())[0]
        response = await self.client.post('/api/jobs', headers=self.headers, json={'workflow_id': workflow['id'], 'description': '雨夜', 'positive_prompt': 'cat', 'negative_prompt': '', 'values': {'MODEL_NAME': 'not-a-model'}, 'use_api': False})
        self.assertEqual(response.status, 200)
        job = await response.json()
        import asyncio
        for _ in range(50):
            job = await (await self.client.get('/api/jobs/' + job['id'])).json()
            if job['state'] in {'failed', 'done'}:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(job['state'], 'failed')
        self.assertEqual(job['description'], '雨夜')
        self.assertEqual(job['positive_prompt'], 'cat')
        self.assertNotIn('api_key', str(job))

    async def test_successful_task_is_persisted(self):
        self.mode = 'good'
        workflow = (await (await self.client.get('/api/workflows')).json())[0]
        job = await (await self.client.post('/api/jobs', headers=self.headers, json={'workflow_id': workflow['id'], 'positive_prompt': 'cat', 'use_api': False})).json()
        import asyncio
        for _ in range(50):
            job = await (await self.client.get('/api/jobs/' + job['id'])).json()
            if job['state'] in {'done', 'failed'}:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(job['state'], 'done')
        self.assertEqual(job['images'][0]['filename'], 'image.png')
        from storage import Store
        self.assertEqual(Store(self.directory.name).jobs[job['id']]['prompt_id'], 'mock-id')

    async def test_temporary_history_failure_keeps_task_pending(self):
        self.mode = 'offline'
        workflow = (await (await self.client.get('/api/workflows')).json())[0]
        job = await (await self.client.post('/api/jobs', headers=self.headers, json={'workflow_id': workflow['id'], 'positive_prompt': 'cat', 'use_api': False})).json()
        import asyncio
        await asyncio.sleep(0.2)
        job = await (await self.client.get('/api/jobs/' + job['id'])).json()
        self.assertEqual(job['state'], 'queued')
        self.assertTrue(job.get('notice'))

    async def test_unconfirmed_submission_is_unknown(self):
        self.mode = 'unconfirmed'
        workflow = (await (await self.client.get('/api/workflows')).json())[0]
        job = await (await self.client.post('/api/jobs', headers=self.headers, json={'workflow_id': workflow['id'], 'positive_prompt': 'cat', 'use_api': False})).json()
        import asyncio
        await asyncio.sleep(0.1)
        job = await (await self.client.get('/api/jobs/' + job['id'])).json()
        self.assertEqual(job['state'], 'unknown')

    async def test_api_prompt_reaches_comfy_graph(self):
        self.mode = 'good'
        await self.client.put('/api/config', json={'api_url': str(self.comfy.make_url('/v1')), 'model': 'mock-model'}, headers=self.headers)
        workflow = (await (await self.client.get('/api/workflows')).json())[0]
        await self.client.put('/api/workflows/' + workflow['id'], json={'fixed_positive': 'best quality', 'fixed_negative': 'watermark'}, headers=self.headers)
        job = await (await self.client.post('/api/jobs', headers=self.headers, json={'workflow_id': workflow['id'], 'description': '海边灯塔', 'use_api': True})).json()
        import asyncio
        for _ in range(50):
            job = await (await self.client.get('/api/jobs/' + job['id'])).json()
            if job['state'] in {'done', 'failed'}:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(job['state'], 'done')
        self.assertEqual(self.submitted_graph['2']['inputs']['text'], 'best quality, watercolor lighthouse')
        self.assertEqual(self.submitted_graph['3']['inputs']['text'], 'watermark, blurry')
        self.assertEqual(self.prompt_request['messages'][1]['content'], '海边灯塔')

    async def test_workflow_without_prompt_binding_is_blocked(self):
        workflow = await (await self.client.post('/api/workflows', json={'name': 'unmapped', 'graph': {'1': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'old static text'}}}}, headers=self.headers)).json()
        response = await self.client.post('/api/jobs', json={'workflow_id': workflow['id'], 'positive_prompt': 'new text'}, headers=self.headers)
        self.assertEqual(response.status, 400)
        self.assertIn('映射', (await response.json())['error'])


if __name__ == '__main__':
    unittest.main()
