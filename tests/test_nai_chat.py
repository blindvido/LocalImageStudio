import sys,unittest,tempfile,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from aiohttp.test_utils import TestServer,TestClient
from studio_server import create_app,STATE
from nai_chat import PARTS

class NaiChatTests(unittest.IsolatedAsyncioTestCase):
 async def test_chat_settings_freeze_validation_and_no_image_jobs(self):
  received=[]
  result={'summary':'已修改动作','sections':{k:{'positive':'new '+k,'negative':''} for k in PARTS},'characters':[{'character_id':'belle','positive':'changed appearance','direction':'walking ahead','negative':'changed','center':{'x':.5,'y':.5}}]}
  async def upstream(request):
   received.append(await request.json())
   return web.json_response({'choices':[{'message':{'content':__import__('json').dumps(result)}}]})
  fake=web.Application();fake.router.add_post('/v1/chat/completions',upstream)
  with tempfile.TemporaryDirectory() as folder:
   async with TestServer(fake) as server,TestClient(TestServer(create_app(folder))) as client:
    bootstrap=await(await client.get('/api/bootstrap')).json();headers={'X-Studio-Token':bootstrap['token']};store=client.server.app[STATE].store
    store.config.update(api_url=str(server.make_url('/v1')),model='fake-llm',api_key='private-test-key')
    settings=await(await client.get('/api/nai/chat/settings')).json();self.assertNotIn('api_key',settings);self.assertTrue(settings['has_api_key'])
    response=await client.put('/api/nai/chat/settings',json={'presets':{'custom':{'name':'分镜','text':'只改动作'}}},headers=headers);self.assertEqual(response.status,200)
    self.assertEqual(store.config['api_key'],'private-test-key')
    current={'sections':{k:{'positive':' 1.3::{locked}:: ' if k=='style' else 'old '+k,'negative':' [bad] ' if k=='style' else ''} for k in PARTS},'characters':[{'character_id':'belle','positive':' exact identity ','direction':'standing','negative':' exact negative ','center':{'x':.5,'y':.5}}]}
    body={'message':'让铃走前面','preset':'custom','current':current,'locked_sections':['style'],'locked_characters':['belle'],'history':[{'role':'user','content':'空洞废墟'}]}
    response=await client.post('/api/nai/chat',json=body,headers=headers);self.assertEqual(response.status,200,await response.text());out=await response.json()
    self.assertEqual(out['sections']['style'],current['sections']['style'])
    self.assertEqual(out['characters'][0]['positive'],' exact identity ')
    self.assertEqual(out['characters'][0]['negative'],' exact negative ')
    self.assertEqual(out['characters'][0]['direction'],'walking ahead')
    self.assertFalse(store.jobs);self.assertEqual(received[0]['model'],'fake-llm');self.assertIn('空洞废墟',received[0]['messages'][1]['content'])
    result['characters']=[]
    response=await client.post('/api/nai/chat',json=body,headers=headers);self.assertEqual(response.status,400)
    result.pop('sections')
    response=await client.post('/api/nai/chat',json={**body,'locked_characters':[]},headers=headers);self.assertEqual(response.status,400)
    self.assertFalse(store.jobs)

if __name__=='__main__':unittest.main()
