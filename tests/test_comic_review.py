import tempfile
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import Store
from comic_review import import_script, set_review, board, selection_export, bind_image
import asyncio
import io
import zipfile
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
from studio_server import create_app, STATE


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        import_script(self.store, {'project':'漫画','chapter':'CH01','version':'v1.6','pages':[
            {'page_id':'P01','panel_id':'K01','visual':'行走','dialogue':'你好','final_prompt':'walking'},
            {'page_id':'P02','panel_id':'K01','visual':'回头','final_prompt':'looking back'}]})
        self.panel = next(iter(self.store.panels))
        self.store.jobs['j1'] = {'id':'j1','panel_id':self.panel,'state':'done','images':[{'filename':'a.png'},{'filename':'b.png'}], 'provider':'novelai','values':{'seed':1},'created_at':'2026-10-20'}
        self.store.jobs['j2'] = {**self.store.jobs['j1'],'id':'j2','images':[{'filename':'c.png'}]}

    def test_approve_specific_image_replace_revoke_persist(self):
        set_review(self.store, 'j1', 1, 'approved', '', self.store.comic_review['revision'])
        selected=selection_export(self.store)['selected']
        self.assertEqual((selected[0]['job_id'],selected[0]['image_index']),('j1',1))
        set_review(self.store, 'j2', 0, 'approved', '采用新版', self.store.comic_review['revision'])
        self.assertEqual([a['job_id'] for a in selection_export(self.store)['selected']],['j2'])
        self.assertEqual(Store(self.tmp.name).comic_review,self.store.comic_review)
        set_review(self.store, 'j2', 0, 'pending', '', self.store.comic_review['revision'])
        self.assertFalse(selection_export(self.store)['selected'])
        self.assertEqual(len(selection_export(self.store)['missing']),2)

    def test_reject_invalid_stale_and_wrong_binding(self):
        for job,index in [('j1',2),('missing',0)]:
            with self.assertRaises(ValueError): set_review(self.store,job,index,'approved','',self.store.comic_review['revision'])
        with self.assertRaises(ValueError): set_review(self.store,'j1',0,'approved','',-1)
        with self.assertRaises(ValueError): bind_image(self.store,'j1',0,'missing',self.store.comic_review['revision'])
        self.store.jobs['j2']['state']='failed'
        with self.assertRaises(ValueError): set_review(self.store,'j2',0,'approved','',self.store.comic_review['revision'])

    def test_script_import_idempotent_versions_and_unassigned(self):
        source={'project':'漫画','chapter':'CH01','version':'v1.6','pages':[{'page_id':'P01','panel_id':'K01','visual':'行走','final_prompt':'walking'}]}
        import_script(self.store,source)
        self.assertEqual(len(self.store.panels),2)
        import_script(self.store,{**source,'version':'v1.7'})
        self.assertEqual(len(self.store.panels),3)
        self.store.jobs['j2'].pop('panel_id')
        self.assertEqual(len(board(self.store)['unassigned']),1)
        bind_image(self.store,'j2',0,self.panel,self.store.comic_review['revision'])
        self.assertFalse(board(self.store)['unassigned'])

    def test_multi_panel_script_and_literal_prompt(self):
        import_script(self.store,{'project':'双格','chapter':'02','version':'v1','pages':[{'page_id':'P03','panels':[
            {'panel_id':'K01','visual':'一','final_prompt':'1.2::{exact}::'},
            {'panel_id':'K02','visual':'二','dialogue':[{'speaker':'哲','text':'好'}]}]}]})
        panels=[p for p in self.store.panels.values() if p['project']=='双格']
        self.assertEqual(len(panels),2)
        self.assertEqual(panels[0]['nai_prompt']['base_positive'],'1.2::{exact}::')
        self.assertEqual(panels[1]['dialogue'][0]['text'],'好')


class ReviewRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_regenerate_idempotent_real_payload_selection_and_delete_guard(self):
        received=[]
        raw=io.BytesIO();Image.new('RGB',(64,64),'green').save(raw,'PNG')
        async def upstream(request):
            received.append(await request.json())
            result=io.BytesIO()
            with zipfile.ZipFile(result,'w') as z:z.writestr('0.png',raw.getvalue())
            return web.Response(body=result.getvalue(),content_type='application/zip')
        fake=web.Application();fake.router.add_post('/ai/generate-image',upstream)
        with tempfile.TemporaryDirectory() as folder:
            async with TestServer(fake) as server,TestClient(TestServer(create_app(folder))) as client:
                headers={'X-Studio-Token':(await (await client.get('/api/bootstrap')).json())['token']}
                studio=client.server.app[STATE]
                studio.store.config.update(novelai_token='test',novelai_url=str(server.make_url('')).rstrip('/'),novelai_fixed_positive='MUST_NOT_REAPPLY')
                import_script(studio.store,{'project':'x','chapter':'CH01','version':'v1','pages':[{'page_id':'P01','final_prompt':'flower'}]})
                pid=next(iter(studio.store.panels))
                from novelai import DEFAULTS
                source={'id':'source','panel_id':pid,'state':'done','provider':'novelai','images':[{'filename':'0.png'}],'values':{**DEFAULTS,'seed':42},'positive_prompt':'flower','negative_prompt':'blur','created_at':'2026-10-20','workflow':{'id':'novelai','name':'NovelAI','graph':{},'mappings':{},'template':'tags','values':dict(DEFAULTS)},'description':'flower','use_api':False,'comfy_url':''}
                studio.persist_job(source)
                path=Path(folder)/'generated/novelai/source/0.png';path.parent.mkdir(parents=True);path.write_bytes(raw.getvalue())
                rev=studio.store.comic_review['revision']
                data={'job_id':'source','image_index':0,'status':'approved','revision':rev,'note':'用户测试'}
                self.assertEqual((await client.post('/api/comic-review/decision',json=data,headers=headers)).status,200)
                self.assertEqual((await client.post('/api/comic-review/decision',json=data,headers=headers)).status,400)
                selected=await (await client.get('/api/comic-review/selection')).json()
                self.assertEqual(selected['selected'][0]['image_path'],str(path.resolve()))
                self.assertEqual((await client.delete('/api/jobs/source',headers=headers)).status,400)
                request={'job_id':'source','image_index':0,'request_id':'retry-test'}
                first,second=await asyncio.gather(*[client.post('/api/comic-review/regenerate',json=request,headers=headers) for _ in range(2)])
                self.assertEqual(first.status,200,await first.text());self.assertEqual(second.status,200)
                j=await first.json();self.assertEqual(j['id'],(await second.json())['id'])
                for _ in range(160):
                    j=await (await client.get('/api/jobs/'+j['id'])).json()
                    if j['state'] in ('done','failed'):break
                    await asyncio.sleep(.025)
                self.assertEqual(j['state'],'done',j.get('error'))
                self.assertEqual(len(received),1)
                self.assertEqual(received[0]['input'],'flower')
                self.assertNotEqual(j['values']['seed'],42)
                self.assertEqual(path.read_bytes(),raw.getvalue())
                self.assertEqual(selection_export(studio.store)['selected'][0]['job_id'],'source')
                self.assertEqual(next(a for a in board(studio.store)['panels'][0]['candidates'] if a['job_id']==j['id'])['status'],'pending')
                # Editable requests are captured without calling a paid upstream.
                studio.spawn = lambda coro: coro.close()
                edited={**request,'request_id':'edited','random_seed':False,'values':{'seed':123,'cfg_scale':6.5},'nai_prompt':{'base_positive':'1.2::{literal}::','base_negative':'bad','characters':[]}}
                response=await client.post('/api/comic-review/regenerate',json=edited,headers=headers)
                self.assertEqual(response.status,200,await response.text())
                child=await response.json()
                self.assertEqual(child['values']['seed'],123)
                self.assertEqual(child['request_snapshot']['input'],'1.2::{literal}::')
                self.assertEqual(child['positive_prompt'],'1.2::{literal}::')
                self.assertEqual(source['positive_prompt'],'flower')
                redraw=await client.post('/api/comic-review/regenerate',json={**edited,'request_id':'redraw','mode':'img2img','strength':.4},headers=headers)
                self.assertEqual(redraw.status,200,await redraw.text())
                child=await redraw.json()
                self.assertEqual(child['img2img']['strength'],.4)
                self.assertTrue((Path(folder)/'generated/novelai'/child['id']/'source.png').exists())
                self.assertNotIn('image',child['request_snapshot']['parameters'])
                invalid=await client.post('/api/comic-review/regenerate',json={**edited,'request_id':'bad','values':{'cfg_scale':99}},headers=headers)
                self.assertEqual(invalid.status,400)
                first_body={'panel_id':pid,'panel_revision':studio.store.panels[pid]['revision'],'request_id':'first-panel','mode':'txt2img','random_seed':False,'values':{'seed':456},'nai_prompt':{'base_positive':'walking, hollow ruins','base_negative':'text','characters':[]}}
                first_response=await client.post('/api/comic-review/generate',json=first_body,headers=headers)
                self.assertEqual(first_response.status,200,await first_response.text())
                first_job=await first_response.json();self.assertEqual(first_job['panel_id'],pid)
                self.assertEqual(first_job['values']['seed'],456);self.assertEqual(first_job['nai_prompt']['base_positive'],'walking, hollow ruins')
                self.assertEqual(first_job['parent_job_id'],'')
                bad_first=await client.post('/api/comic-review/generate',json={**first_body,'request_id':'stale-first','panel_revision':-1},headers=headers)
                self.assertEqual(bad_first.status,400)

                import base64
                upload={'panel_id':pid,'request_id':'upload-one','filename':'test.png','image':base64.b64encode(raw.getvalue()).decode()}
                for _ in range(2):
                    response=await client.post('/api/comic-review/upload',json=upload,headers=headers)
                    self.assertEqual(response.status,200,await response.text())
                uploads=[x for x in studio.store.jobs.values() if x.get('provider')=='upload']
                self.assertEqual(len(uploads),1)
                uploaded=uploads[0]
                self.assertNotIn('seed',uploaded['values'])
                image=await client.get('/api/images/'+uploaded['id']+'/0')
                self.assertEqual(image.status,200)
                self.assertEqual(Image.open(io.BytesIO(await image.read())).size,(64,64))
                self.assertEqual((Path(folder)/'generated/uploads'/uploaded['id']/'original.png').read_bytes(),raw.getvalue())
                bad=await client.post('/api/comic-review/upload',json={**upload,'request_id':'bad-image','image':base64.b64encode(b'not an image').decode()},headers=headers)
                self.assertEqual(bad.status,400)

                self.assertEqual(selection_export(studio.store)['selected'][0]['job_id'],'source')



if __name__=='__main__': unittest.main()
