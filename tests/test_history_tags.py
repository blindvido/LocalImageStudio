import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from history_tags import effective_tags,clean_tags,tagged
from aiohttp.test_utils import TestClient,TestServer
from studio_server import create_app,STATE


class TagTests(unittest.TestCase):
    def test_literal_inference_and_manual_override(self):
        job={'positive_prompt':'wise (zenless_zone_zero), mushoku tensei','negative_prompt':'another work'}
        self.assertEqual(effective_tags(job),['绝区零','无职转生'])
        self.assertEqual(effective_tags({**job,'work_tags':[]}),[])
        self.assertEqual(effective_tags({'negative_prompt':'zenless zone zero'}),[])
        self.assertEqual(effective_tags({'nai_prompt':{'characters':[{'positive':'belle (zenless zone zero)'}]}}),['绝区零'])
        self.assertEqual(tagged(job)['work_tags_source'],'inferred')
        self.assertNotIn('work_tags',job)

    def test_tag_validation(self):
        self.assertEqual(clean_tags([' 原创 ','原创','Test','test']),['原创','Test'])
        for bad in ('str',[None],[''],['a'*41],['x']*11):
            with self.assertRaises(ValueError):clean_tags(bad)


class TagRoutes(unittest.IsolatedAsyncioTestCase):
    async def test_persistence_and_generation_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            async with TestClient(TestServer(create_app(folder))) as c:
                s=c.server.app[STATE]
                original={'id':'tag-test','created_at':'2026-10-22','state':'done','positive_prompt':'zenless zone zero','negative_prompt':'test','values':{'seed':42},'images':[]}
                s.persist_job(dict(original))
                h={'X-Studio-Token':(await (await c.get('/api/bootstrap')).json())['token']}
                response=await c.put('/api/jobs/tag-test/work-tags',json={'tags':['无职转生','原创']},headers=h)
                self.assertEqual(response.status,200,await response.text())
                jobs=await (await c.get('/api/jobs')).json()
                self.assertEqual(jobs[0]['work_tags'],['无职转生','原创'])
                self.assertEqual(jobs[0]['values'],original['values'])
                self.assertEqual(jobs[0]['positive_prompt'],original['positive_prompt'])
                from storage import Store
                self.assertEqual(Store(folder).jobs['tag-test']['work_tags'],['无职转生','原创'])
                self.assertEqual((await c.put('/api/jobs/missing/work-tags',json={'tags':[]},headers=h)).status,404)
                self.assertEqual((await c.put('/api/jobs/tag-test/work-tags',json={'tags':['a'*41]},headers=h)).status,400)


if __name__=='__main__':unittest.main()
