import sys, tempfile, unittest, io, zipfile, json, importlib.util, copy, os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from storage import Store
from comic_review import import_script, set_review, board
from comic_editor import save_chapter, chapter_pages, export_bundle

class ComicEditorTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Store(self.tmp.name)
  self.where={'project':'测试漫画','chapter':'CH01','script_version':'v1'}
  import_script(self.store,{'project':'测试漫画','chapter':'CH01','version':'v1','pages':[{'page_id':'P01','visual':'行走','final_prompt':'1.2::{exact}::'},{'page_id':'P02','visual':'回头','final_prompt':'turning'}]})
  self.ids=list(self.store.panels)
  self.store.jobs['source']={'id':'source','state':'done','provider':'novelai','panel_id':self.ids[0],'images':[{'filename':'0.png'}],'values':{'seed':42},'positive_prompt':'actual','negative_prompt':'','created_at':'2026-10-22'}
  set_review(self.store,'source',0,'approved','采用',self.store.comic_review['revision'])
 def save(self,pages):return save_chapter(self.store,{**self.where,'revision':self.store.comic_review['revision'],'pages':pages})
 def test_move_edit_empty_page_delete_detaches_and_audits(self):
  old=copy.deepcopy(self.store.jobs['source']);p=chapter_pages(self.store,self.where)
  moved=p[0]['panels'].pop();moved['dialogue']=[{'speaker':'哲','text':'走吧。','type':'dialogue'}];moved['camera_notes']='中景';p[1]['panels'].append(moved);p.insert(0,{'panels':[]})
  self.save(p)
  self.assertEqual(self.store.panels[self.ids[0]]['page'],'P03')
  self.assertEqual(self.store.panels[self.ids[0]]['panel'],'K02')
  self.assertEqual(self.store.jobs['source'],old)
  self.assertEqual(len(chapter_pages(self.store,self.where)),3)
  self.assertEqual(board(self.store)['panels'][-1]['candidates'][0]['status'],'approved')
  p=chapter_pages(self.store,self.where);p[-1]['panels'].pop();self.save(p)
  self.assertEqual(board(self.store)['unassigned'][0]['job_id'],'source')
  self.assertEqual(board(self.store)['unassigned'][0]['status'],'pending')
  self.assertEqual(self.store.comic_review['events'][-1]['before'][-1]['panels'][-1]['dialogue'][0]['text'],'走吧。')
  reloaded=Store(self.tmp.name);self.assertEqual(reloaded.panels,self.store.panels)
 def test_invalid_atomic_stale_scope_duplicate(self):
  before=copy.deepcopy(self.store.panels);pages=chapter_pages(self.store,self.where);pages[1]['panels'].append(pages[0]['panels'][0])
  with self.assertRaises(ValueError):self.save(pages)
  self.assertEqual(before,self.store.panels)
  with self.assertRaises(ValueError):save_chapter(self.store,{**self.where,'revision':-1,'pages':[]})
  self.assertEqual(before,self.store.panels)
 def test_export_roundtrip_and_real_skill_prepare(self):
  skill_path=Path(os.environ.get('COMIC_PIPELINE_SCRIPT',str(Path.home()/'.codex/skills/comic-storyboard-image-pipeline/scripts/panel_bundle.py')))
  if not skill_path.is_file(): self.skipTest('Optional comic pipeline Skill not installed; set COMIC_PIPELINE_SCRIPT')
  content=export_bundle(self.store,self.where)
  with zipfile.ZipFile(io.BytesIO(content)) as z:
   z.extractall(Path(self.tmp.name)/'export');script=json.loads(z.read('script/storyboard.json'));manifest=json.loads(z.read('manifest.json'))
  count=len(self.store.panels);import_script(self.store,script);self.assertEqual(len(self.store.panels),count)
  spec=importlib.util.spec_from_file_location('pipeline',skill_path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  root=Path(self.tmp.name)/'export'
  result=module.prepare(root,input_path=root/manifest['panels'][0]['input'])
  snapshot=json.loads(Path(result['snapshot']).read_text('utf-8'))
  self.assertEqual(snapshot['final_prompt']['base_positive'],'1.2::{exact}::')
  self.assertTrue(snapshot['source_snapshots'][0]['sha256'])
  self.assertEqual(snapshot['resolved_spec']['dialogue'],'')
  self.assertTrue(manifest['panels'][0]['legacy_flat_prompt'])
  fresh=Store(Path(self.tmp.name)/'fresh');import_script(fresh,script);self.assertEqual(len(fresh.panels),2)
 def test_new_panels_and_blank_pages(self):
  self.save([{'panels':[]},{'panels':[{'visible_description':'新镜头','dialogue':[{'speaker':'','text':'轰','type':'sfx'}]}]}])
  p=chapter_pages(self.store,self.where);self.assertEqual(len(p),2);self.assertEqual(p[1]['panels'][0]['panel'],'K01')
  with zipfile.ZipFile(io.BytesIO(export_bundle(self.store,self.where))) as z:self.assertFalse(json.loads(z.read('manifest.json'))['panels'][0]['ready_for_prepare'])


class ComicEditorRoutes(unittest.IsolatedAsyncioTestCase):
 async def test_save_export_http_and_empty_page_roundtrip(self):
  from aiohttp.test_utils import TestClient,TestServer
  from studio_server import create_app,STATE
  with tempfile.TemporaryDirectory() as folder:
   async with TestClient(TestServer(create_app(folder))) as client:
    bootstrap=await (await client.get('/api/bootstrap')).json();headers={'X-Studio-Token':bootstrap['token']}
    store=client.server.app[STATE].store
    where={'project':'测试','chapter':'CH01','script_version':'v1'}
    body={**where,'revision':0,'pages':[{'panels':[]},{'panels':[{'visible_description':'行走','dialogue':[{'speaker':'哲','text':'走吧','type':'dialogue'}]}]}]}
    response=await client.post('/api/comic-editor/save',json=body,headers=headers)
    self.assertEqual(response.status,200,await response.text())
    self.assertEqual(len((await response.json())['page_catalog']),2)
    self.assertEqual((await client.post('/api/comic-editor/save',json=body,headers=headers)).status,400)
    response=await client.get('/api/comic-editor/export',params=where)
    self.assertEqual(response.status,200)
    with zipfile.ZipFile(io.BytesIO(await response.read())) as z:
     script=json.loads(z.read('script/storyboard.json'));self.assertIn('inputs/P02/K01.json',z.namelist())
    fresh=Store(Path(folder)/'fresh');import_script(fresh,script)
    self.assertEqual(len(chapter_pages(fresh,where)),2)
    self.assertEqual(chapter_pages(fresh,where)[1]['panels'][0]['dialogue'][0]['text'],'走吧')

if __name__=='__main__':unittest.main()

