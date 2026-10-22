import tempfile
import unittest
from aiohttp.test_utils import TestClient, TestServer
from studio_server import create_app
from storage import Store


class PromptPresetTests(unittest.IsolatedAsyncioTestCase):
    async def test_crud_preserves_exact_syntax_and_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            async with TestClient(TestServer(create_app(folder))) as client:
                token = (await (await client.get('/api/bootstrap')).json())['token']
                headers = {'X-Studio-Token': token}
                original = '  1.5::artist_(name)::, {{{glow}}}, [soft]\nsource#holding hands, \\(tag\\), | <x>  '
                body = {'name': '测试预设', 'category': 'style', 'text': original}
                response = await client.post('/api/prompt-presets', json=body, headers=headers)
                self.assertEqual(response.status, 200)
                record = await response.json()
                self.assertEqual(record['text'], original)
                self.assertEqual(Store(folder).prompt_presets[record['id']]['text'], original)
                record_url = '/api/prompt-presets/' + record['id']
                response = await client.put(record_url, json={**body, 'category': 'action', 'text': 'mutual#hugging\n  '}, headers=headers)
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())['text'], 'mutual#hugging\n  ')
                for invalid in ({**body, 'category': 'other'}, {**body, 'text': ''}):
                    response = await client.post('/api/prompt-presets', json=invalid, headers=headers)
                    self.assertEqual(response.status, 400)
                response = await client.post('/api/prompt-presets', json=body)
                self.assertEqual(response.status, 403)
                response = await client.delete(record_url, headers=headers)
                self.assertEqual(response.status, 200)
                self.assertEqual(await (await client.get('/api/prompt-presets')).json(), [])
                self.assertEqual(Store(folder).prompt_presets, {})

    async def test_separate_negative_only_and_legacy_presets(self):
        with tempfile.TemporaryDirectory() as folder:
            async with TestClient(TestServer(create_app(folder))) as client:
                token = (await (await client.get('/api/bootstrap')).json())['token']
                headers = {'X-Studio-Token':token}
                negative = '  -1.2::text::, [blur]\n{{extra arms}}  '
                for positive in ('1.2::lineart::', ''):
                    response = await client.post('/api/prompt-presets',json={'name':'paired','category':'style','text':positive,'negative_text':negative},headers=headers)
                    self.assertEqual(response.status,200)
                    record = await response.json()
                    self.assertEqual(record['text'],positive)
                    self.assertEqual(Store(folder).prompt_presets[record['id']]['negative_text'],negative)
                response = await client.post('/api/prompt-presets',json={'name':'legacy','category':'action','text':'walking'},headers=headers)
                self.assertEqual((await response.json())['negative_text'],'')
