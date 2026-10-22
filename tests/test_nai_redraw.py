import asyncio
import base64
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
from studio_server import create_app, STATE


class RedrawTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_route_source_preserved_and_duplicate_safe(self):
        png = io.BytesIO(); Image.new('RGB', (128, 192), 'green').save(png, 'PNG')
        received = []
        async def upstream(request):
            received.append(await request.json())
            result = io.BytesIO()
            with zipfile.ZipFile(result, 'w') as z: z.writestr('0.png', png.getvalue())
            return web.Response(body=result.getvalue(), content_type='application/zip')
        fake = web.Application(); fake.router.add_post('/ai/generate-image', upstream)
        with tempfile.TemporaryDirectory() as folder:
            async with TestServer(fake) as server, TestClient(TestServer(create_app(folder))) as client:
                token = (await (await client.get('/api/bootstrap')).json())['token']
                headers = {'X-Studio-Token': token}
                studio = client.server.app[STATE]
                studio.store.config.update(novelai_token='test-token', novelai_url=str(server.make_url('')).rstrip('/'))
                source = {'id':'parent', 'state':'done', 'provider':'novelai', 'images':[{'filename':'0.png'}],
                          'values':{'seed':42}, 'positive_prompt':'flower', 'negative_prompt':'blur',
                          'created_at':'2026-10-15T00:00:00+08:00',
                          'nai_prompt': {'base_positive':'flower', 'base_negative':'blur', 'characters':[
                              {'character_id':'a','positive':'1girl','negative':'hat','direction':'walking','center':{'x':.5,'y':.5}}]}}
                studio.persist_job(source)
                path = Path(folder)/'generated/novelai/parent/0.png'; path.parent.mkdir(parents=True); path.write_bytes(png.getvalue())
                data = {'source_job_id':'parent','source_index':0,'strength':.35,'request_id':'test-redraw'}
                for invalid in (-.1, 1.1, '0.3', True):
                    response=await client.post('/api/nai/redraw',json={**data,'strength':invalid},headers=headers)
                    self.assertEqual(response.status,400)
                first, second = await asyncio.gather(*[client.post('/api/nai/redraw',json=data,headers=headers) for _ in range(2)])
                self.assertEqual(first.status,200,await first.text())
                job=await first.json(); self.assertEqual(job['id'],(await second.json())['id'])
                for _ in range(80):
                    job=await (await client.get('/api/jobs/'+job['id'])).json()
                    if job['state'] in ('done','failed'):break
                    await asyncio.sleep(.025)
                self.assertEqual(job['state'],'done',job.get('error'))
                self.assertEqual(len(received),1)
                body=received[0]; self.assertEqual(body['action'],'img2img')
                self.assertEqual(body['parameters']['strength'],.35)
                self.assertEqual(body['parameters']['seed'],42)
                self.assertEqual(body['parameters']['v4_prompt']['caption']['char_captions'][0]['char_caption'],'1girl, walking')
                with Image.open(io.BytesIO(base64.b64decode(body['parameters']['image']))) as im:self.assertEqual(im.size,(128,192))
                self.assertEqual(path.read_bytes(),png.getvalue())
                self.assertEqual(job['parent_job_id'],'parent')
                self.assertNotIn('image',job['request_snapshot']['parameters'])
                for key in ('parent',job['id']):
                    response=await client.get('/api/images/'+key+'/0');self.assertEqual(response.status,200)
                self.assertEqual((await client.delete('/api/jobs/parent',headers=headers)).status,400)
                self.assertEqual(len(studio.store.jobs),2)


if __name__=='__main__':unittest.main()
