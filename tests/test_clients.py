import unittest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from clients import generate_prompt, ComfyClient, completion_url


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received = []
        self.mode = 'good'

        async def completion(request):
            self.received.append((request.headers.get('Authorization'), await request.json()))
            if self.mode == 'auth':
                return web.json_response({'error': {'message': 'secret-value'}}, status=401)
            if self.mode == 'refusal':
                return web.json_response({'choices': [{'finish_reason':'stop', 'message': {'content':'I cannot fulfill this request. The provided character specifications include explicit sexual content.'}}]})
            if self.mode == 'truncated':
                return web.json_response({'choices': [{'finish_reason':'length', 'message': {'content':'{"positive_prompt":"unfinished'}}]})
            if self.mode in {'structured_refusal', 'filtered', 'max_tokens'}:
                return web.json_response({'choices': [{'finish_reason':{'filtered':'content_filter','max_tokens':'max_tokens'}.get(self.mode,'stop'), 'message': {'content':None, 'refusal':'Cannot comply' if self.mode=='structured_refusal' else None}}]})
            content = '```json\n{"positive_prompt":"rainy city", "negative_prompt":"blurry"}\n```' if self.mode == 'good' else 'not json'
            return web.json_response({'choices': [{'message': {'content': content}}]})

        async def prompt(request):
            self.received.append(await request.json())
            if self.mode == 'bad_graph':
                return web.json_response({'error': {'message': 'missing node'}, 'node_errors': {}}, status=400)
            return web.json_response({'prompt_id': 'test-job', 'number': 0, 'node_errors': {}})

        async def history(request):
            if self.mode == 'execution_error':
                return web.json_response({'test-job': {'status': {'status_str': 'error', 'completed': False, 'messages': [['execution_error', {'node_id': '2', 'exception_message': 'CUDA out of memory'}]]}, 'outputs': {}}})
            return web.json_response({'test-job': {'status': {'status_str': 'success', 'completed': True}, 'outputs': {'8': {'images': [{'filename': 'a.png', 'subfolder': 'x', 'type': 'output'}]}, '9': {'images': [{'filename': 'b.png', 'subfolder': '', 'type': 'output'}]}}}})

        app = web.Application()
        app.router.add_post('/v1/chat/completions', completion)
        app.router.add_post('/prompt', prompt)
        app.router.add_get('/history/{id}', history)
        self.server = TestServer(app)
        await self.server.start_server()
        self.session = ClientSession()
        self.base = str(self.server.make_url('')).rstrip('/')

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.close()

    async def test_prompt_protocol_and_code_fence(self):
        result = await generate_prompt(self.session, {'api_url': self.base + '/v1', 'api_key': 'secret', 'model': 'test-model'}, '雨夜城市', 'system text')
        self.assertEqual(result, {'positive_prompt': 'rainy city', 'negative_prompt': 'blurry'})
        auth, payload = self.received[0]
        self.assertEqual(auth, 'Bearer secret')
        self.assertEqual(payload['model'], 'test-model')
        self.assertEqual(payload['messages'][1]['content'], '雨夜城市')

    async def test_auth_error_does_not_echo_secret(self):
        self.mode = 'auth'
        with self.assertRaises(ValueError) as caught:
            await generate_prompt(self.session, {'api_url': self.base + '/v1', 'api_key': 'secret-value', 'model': 'test'}, 'cat', 'system')
        self.assertNotIn('secret-value', str(caught.exception))
        self.assertIn('401', str(caught.exception))

    async def test_invalid_prompt_response(self):
        self.mode = 'bad_content'
        with self.assertRaisesRegex(ValueError, '格式'):
            await generate_prompt(self.session, {'api_url': self.base + '/v1', 'api_key': '', 'model': 'test'}, 'cat', 'system')

    async def test_refusal_is_not_reported_as_invalid_json(self):
        self.mode = 'refusal'
        with self.assertRaisesRegex(ValueError, '拒绝') as caught:
            await generate_prompt(self.session, {'api_url':self.base+'/v1','model':'test'}, 'cat', 'system')
        self.assertNotIn('格式不正确', str(caught.exception))

    async def test_truncation_has_specific_error(self):
        self.mode = 'truncated'
        with self.assertRaisesRegex(ValueError, '截断'):
            await generate_prompt(self.session, {'api_url':self.base+'/v1','model':'test'}, 'cat', 'system')

    async def test_structured_refusal_and_finish_reasons(self):
        for mode, expected in [('structured_refusal','拒绝'),('filtered','拒绝'),('max_tokens','截断')]:
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, expected):
                await generate_prompt(self.session, {'api_url':self.base+'/v1','model':'test'}, 'cat', 'system')

    async def test_full_api_url_preserved(self):
        self.assertEqual(completion_url('https://example.com/v1/chat/completions/'), 'https://example.com/v1/chat/completions')

    async def test_submit_and_multiple_images(self):
        client = ComfyClient(self.session, self.base)
        graph = {'1': {'class_type': 'SaveImage', 'inputs': {}}}
        self.assertEqual(await client.submit(graph), 'test-job')
        self.assertEqual(self.received[0]['prompt'], graph)
        self.assertEqual([x['filename'] for x in client.images(await client.history('test-job'))], ['a.png', 'b.png'])

    async def test_rejected_graph_not_success(self):
        self.mode = 'bad_graph'
        with self.assertRaisesRegex(ValueError, 'missing node'):
            await ComfyClient(self.session, self.base).submit({})

    async def test_execution_error_reported(self):
        self.mode = 'execution_error'
        with self.assertRaisesRegex(ValueError, 'CUDA out of memory'):
            ComfyClient.images(await ComfyClient(self.session, self.base).history('test-job'))
