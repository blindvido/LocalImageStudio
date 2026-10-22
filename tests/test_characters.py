import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from aiohttp.test_utils import TestClient, TestServer
from tests import test_server as fixtures
from studio_server import STATE, create_app


class CharacterServerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ServerTests.asyncSetUp
    asyncTearDown = fixtures.ServerTests.asyncTearDown
    async def save_character(self, **changes):
        data = {'name': 'DS', 'aliases': ['DeepSeek', '深海'], 'appearance': 'long blue hair, whale-shaped ears, blue whale earrings', 'clothing': 'blue cheongsam', 'negative_prompt': '', 'notes': '', 'enabled': True, **changes}
        response = await self.client.post('/api/characters', headers=self.headers, json=data)
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    async def test_library_persists_and_matches_literal_names_without_substring_collisions(self):
        ds = await self.save_character()
        await self.save_character(name='GPT', aliases=['小白'], appearance='silver hair, crystal earrings')
        response = await self.client.post('/api/characters/match', headers=self.headers, json={'description': 'ds和小白去打工，DeepSeek拿面包'})
        matched = (await response.json())['characters']
        self.assertEqual({x['name'] for x in matched}, {'DS', 'GPT'})
        response = await self.client.post('/api/characters/match', headers=self.headers, json={'description': 'ADSL和ChatGPT都不是名字匹配'})
        self.assertEqual((await response.json())['characters'], [])
        from storage import Store
        self.assertEqual(Store(self.directory.name).characters[ds['id']]['appearance'], ds['appearance'])
        await self.client.put('/api/characters/'+ds['id'], headers=self.headers, json={'enabled': False})
        response = await self.client.post('/api/characters/match', headers=self.headers, json={'description': '深海'})
        self.assertEqual((await response.json())['characters'], [])

    async def test_regex_metacharacters_are_literal_and_aliases_unique(self):
        await self.save_character(name='A+B', aliases=['(小蓝)'])
        result = await (await self.client.post('/api/characters/match', headers=self.headers, json={'description':'A+B和(小蓝)'})).json()
        self.assertEqual(len(result['characters']), 1)
        result = await (await self.client.post('/api/characters/match', headers=self.headers, json={'description':'AAAB和小蓝'})).json()
        self.assertEqual(result['characters'], [])
        await self.save_character()
        response = await self.client.post('/api/characters', headers=self.headers, json={'name':'另一个','aliases':['ds'],'appearance':'red hair'})
        self.assertEqual(response.status, 400)

    async def test_injection_reaches_prompt_api_and_one_click_job_snapshot(self):
        ds = await self.save_character()
        await self.client.put('/api/config', json={'api_url': str(self.comfy.make_url('/v1')), 'model':'mock'}, headers=self.headers)
        workflow = (await (await self.client.get('/api/workflows')).json())[0]
        result = await (await self.client.post('/api/prompt', headers=self.headers, json={'workflow_id':workflow['id'],'description':'DS穿红色工作服，困倦'})).json()
        self.assertEqual(result['matched_characters'][0]['id'], ds['id'])
        context = self.prompt_request['messages'][0]['content']
        self.assertIn('whale-shaped ears', context)
        self.assertIn('blue cheongsam', context)
        self.assertEqual(self.prompt_request['messages'][1]['content'], 'DS穿红色工作服，困倦')
        self.mode = 'good'
        job = await (await self.client.post('/api/jobs', headers=self.headers, json={'workflow_id':workflow['id'],'description':'DS上班','use_api':True})).json()
        await self.client.delete('/api/characters/'+ds['id'], headers=self.headers)
        for _ in range(50):
            job = await (await self.client.get('/api/jobs/'+job['id'])).json()
            if job['state'] in {'done','failed'}: break
            await asyncio.sleep(.02)
        self.assertEqual(job['state'],'done')
        self.assertEqual(job['matched_characters'][0]['appearance'], ds['appearance'])
        self.assertIn('whale-shaped ears', self.prompt_request['messages'][0]['content'])

    async def test_draft_does_not_save_until_confirmed(self):
        await self.client.put('/api/config', json={'api_url': str(self.comfy.make_url('/v1')), 'model':'mock'}, headers=self.headers)
        response = await self.client.post('/api/characters/draft', headers=self.headers, json={'name':'新人物','description':'成年蓝发女性，鲸鱼耳饰'})
        self.assertEqual(response.status, 200, await response.text())
        self.assertTrue((await response.json())['appearance'])
        self.assertEqual(await (await self.client.get('/api/characters')).json(), [])

    async def test_direct_prompt_generation_keeps_finished_text_unchanged(self):
        await self.save_character()
        self.mode = 'good'
        workflow = (await (await self.client.get('/api/workflows')).json())[0]
        job = await (await self.client.post('/api/jobs', headers=self.headers, json={'workflow_id':workflow['id'],'description':'DS','positive_prompt':'finished red hair prompt','use_api':False})).json()
        for _ in range(50):
            job = await (await self.client.get('/api/jobs/'+job['id'])).json()
            if job['state'] in {'done','failed'}: break
            await asyncio.sleep(.02)
        self.assertEqual(job['state'], 'done')
        self.assertEqual(job['positive_prompt'], 'finished red hair prompt')
        self.assertEqual(job['matched_characters'], [])


class OfflineImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_image_works_with_comfy_offline_and_blocks_traversal(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as portable:
            root = Path(portable); folder = root/'ComfyUI/output/Roommates'; folder.mkdir(parents=True)
            (folder/'panel.png').write_bytes(b'known-image-bytes')
            with patch('studio_server.PORTABLE',root):
                app = create_app(data); studio = app[STATE]
                studio.store.jobs['old'] = {'id':'old','state':'done','images':[{'filename':'panel.png','subfolder':'Roommates','type':'output'}],'comfy_url':'http://127.0.0.1:8188'}
                client = TestClient(TestServer(app)); await client.start_server()
                try:
                    with patch.object(studio.session, 'get', side_effect=AssertionError('offline image must not call ComfyUI')):
                        result = await client.get('/api/images/old/0')
                    self.assertEqual(result.status,200)
                    self.assertEqual(await result.read(),b'known-image-bytes')
                    studio.store.jobs['old']['comfy_url']='http://127.0.0.1:1'
                    result = await client.get('/api/images/old/0')
                    self.assertEqual(result.status,503,'another local installation must not receive unrelated disk images')
                    studio.store.jobs['old']['images'][0]['subfolder']='../../'
                    result = await client.get('/api/images/old/0')
                    self.assertEqual(result.status,400)
                finally: await client.close()


class MatcherTests(unittest.TestCase):
    def test_repeated_names_and_overlapping_chinese_aliases(self):
        from characters import match_characters
        records={'a':{'id':'a','name':'蓝','aliases':[]},'b':{'id':'b','name':'小蓝','aliases':[]}}
        result = match_characters(records, '小蓝'+('蓝'*10000))
        self.assertEqual([x['id'] for x in result], ['b','a'])
