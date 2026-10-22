import unittest
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
import clients
import test_server
from unittest.mock import AsyncMock, patch
from storage import Store


class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_discovery_and_errors(self):
        mode = ['good']
        received = []
        async def models(request):
            received.append(request.headers.get('Authorization'))
            if mode[0] == 'auth':
                return web.json_response({'error': 'secret-key'}, status=401)
            if mode[0] == 'bad':
                return web.json_response({'hello': 'world'})
            return web.json_response({'data': [{'id': 'z'}, {'id': 'a'}, {'id': 'z'}, {'id': None}]})
        app = web.Application()
        app.router.add_get('/v1/models', models)
        async with TestServer(app) as server, ClientSession() as session:
            config = {'api_url': str(server.make_url('/v1/chat/completions')), 'api_key': 'secret-key'}
            self.assertEqual(await clients.list_models(session, config), ['a', 'z'])
            self.assertEqual(received, ['Bearer secret-key'])
            mode[0] = 'auth'
            with self.assertRaisesRegex(ValueError, '401') as caught:
                await clients.list_models(session, config)
            self.assertNotIn('secret-key', str(caught.exception))
            mode[0] = 'bad'
            with self.assertRaisesRegex(ValueError, '模型列表'):
                await clients.list_models(session, config)


class ModelEndpointTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_server.ServerTests.asyncSetUp
    asyncTearDown = test_server.ServerTests.asyncTearDown

    async def test_saved_key_reused_without_exposing_or_saving_candidate(self):
        await self.client.put('/api/config', json={'api_url':'https://example.com/v1', 'api_key':'secret-key', 'model':'current'}, headers=self.headers)
        with patch('studio_server.list_models', new=AsyncMock(return_value=['one', 'two'])) as call:
            response = await self.client.post('/api/models', json={'api_url':'https://example.com/v1'}, headers=self.headers)
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())['models'], ['one', 'two'])
            self.assertEqual(call.call_args.args[1]['api_key'], 'secret-key')
            response = await self.client.post('/api/models', json={'api_url':'https://other.example/v1', 'api_key':'new-key'}, headers=self.headers)
            self.assertEqual(response.status, 200)
            self.assertNotIn('new-key', await response.text())
        self.assertEqual(Store(self.directory.name).config['model'], 'current')
        self.assertEqual(Store(self.directory.name).config['api_key'], 'secret-key')
        response = await self.client.post('/api/models', json={'api_url':'https://other.example/v1'}, headers=self.headers)
        self.assertEqual(response.status, 400)
        self.assertEqual((await self.client.post('/api/models', json={})).status, 403)
