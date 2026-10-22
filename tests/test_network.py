import unittest
from unittest.mock import patch

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from network import SystemProxySession, system_proxy


class ProxyTests(unittest.TestCase):
    @patch('network.proxy_bypass', return_value=False)
    @patch('network.getproxies', return_value={'https': '127.0.0.1:10090'})
    def test_windows_proxy_without_scheme(self, *_):
        self.assertEqual(system_proxy('https://image.novelai.net/ai/generate-image'),
                         'http://127.0.0.1:10090')

    @patch('network.proxy_bypass', return_value=False)
    @patch('network.getproxies', return_value={'http': 'http://proxy:8080'})
    def test_loopback_always_direct(self, *_):
        for host in ('localhost', '127.0.0.1', '127.0.0.2', '[::1]'):
            self.assertIsNone(system_proxy(f'http://{host}:8188/prompt'))

    @patch('network.proxy_bypass', return_value=True)
    @patch('network.getproxies', return_value={'https': 'http://proxy:8080'})
    def test_system_bypass_respected(self, *_):
        self.assertIsNone(system_proxy('https://internal.example/'))

    @patch('network.proxy_bypass', return_value=False)
    @patch('network.getproxies', return_value={})
    def test_without_proxy_direct(self, *_):
        self.assertIsNone(system_proxy('https://image.novelai.net/'))


class ProxyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_request_uses_proxy_and_local_request_bypasses_it(self):
        received = []

        async def proxy(request):
            received.append((request.method, request.raw_path))
            return web.json_response({'via': 'proxy'})

        async def local(request):
            return web.json_response({'via': 'direct'})

        proxy_app = web.Application()
        proxy_app.router.add_route('*', '/{path:.*}', proxy)
        local_app = web.Application()
        local_app.router.add_get('/', local)
        async with TestServer(proxy_app) as proxy_server, TestServer(local_app) as local_server:
            proxies = {'http': str(proxy_server.make_url('')).rstrip('/')}
            with patch('network.getproxies', return_value=proxies), patch('network.proxy_bypass', return_value=False):
                async with ClientSession() as raw:
                    session = SystemProxySession(raw)
                    async with session.post('http://unresolvable.invalid/generate', json={'prompt': 'cat'}) as response:
                        self.assertEqual((await response.json())['via'], 'proxy')
                    async with session.get(str(local_server.make_url('/'))) as response:
                        self.assertEqual((await response.json())['via'], 'direct')
            self.assertEqual(received, [('POST', 'http://unresolvable.invalid/generate')])
