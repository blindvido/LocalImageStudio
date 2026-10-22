import unittest
import novelai
import asyncio
import io
import zipfile
from PIL import Image
from aiohttp import web
from aiohttp.test_utils import TestServer,TestClient
import tempfile
from studio_server import create_app

def image_zip():
    image=io.BytesIO();Image.new('RGB',(64,64),'white').save(image,format='PNG')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr('../ignored-path.png',image.getvalue())
    return out.getvalue()


class NovelAITests(unittest.TestCase):
    def test_v5_payload_and_bounds(self):
        body = novelai.payload('flower', 'blur', {'MODEL_NAME':'nai-diffusion-5-full','width':832,'height':1216,'steps':28,'cfg_scale':5,'seed':7})
        self.assertEqual(body['action'], 'generate')
        self.assertEqual(body['parameters']['v4_prompt']['caption']['base_caption'], 'flower')
        self.assertEqual(body['parameters']['n_samples'], 1)
        with self.assertRaises(ValueError):
            novelai.payload('flower', '', {'width':833})
        with self.assertRaises(ValueError):
            novelai.payload('flower', '', {'seed':2**40})

    def test_fixed_prompt_roundtrip_and_empty_defaults(self):
        config = {'novelai_fixed_positive': 'comic style', 'novelai_fixed_negative': 'text'}
        prompts = novelai.apply_fixed(config, 'flower', 'blur')
        self.assertEqual(prompts, ('comic style, flower', 'text, blur'))
        self.assertEqual(novelai.apply_fixed(config, *prompts), prompts)
        self.assertEqual(novelai.apply_fixed({}, 'flower', ''), ('flower', ''))

    def test_url_handles_full_endpoint(self):
        self.assertEqual(novelai.endpoint('https://example.com/v1/novelai/ai/generate-image'), 'https://example.com/v1/novelai/ai/generate-image')

    def test_zip_validated_without_extracting_paths(self):
        self.assertTrue(novelai.unpack(image_zip()).startswith(b'\x89PNG'))
        with self.assertRaises(ValueError):novelai.unpack(b'not a zip')

class NovelAIIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_token_job_and_offline_image(self):
        received=[]
        async def generate(request):
            received.append((request.headers.get('Authorization'),await request.json()))
            return web.Response(body=image_zip(),content_type='application/zip',status=201)
        fake=web.Application();fake.router.add_post('/ai/generate-image',generate)
        with tempfile.TemporaryDirectory() as folder:
            async with TestServer(fake) as upstream, TestClient(TestServer(create_app(folder))) as client:
                token=(await (await client.get('/api/bootstrap')).json())['token'];headers={'X-Studio-Token':token}
                response=await client.put('/api/config',json={'novelai_url':str(upstream.make_url('')).rstrip('/'),'novelai_token':'secret-nai','novelai_model':'nai-diffusion-4-5-full','novelai_fixed_positive':'comic style','novelai_fixed_negative':'watermark'},headers=headers)
                self.assertNotIn('secret-nai',await response.text())
                response=await client.post('/api/jobs',json={'provider':'novelai','positive_prompt':'flower','use_api':False,'random_seed':False},headers=headers)
                self.assertEqual(response.status,200);j=await response.json()
                for _ in range(30):
                    j=await (await client.get('/api/jobs/'+j['id'])).json()
                    if j['state'] in {'done','failed','unknown'}:break
                    await asyncio.sleep(.05)
                self.assertEqual(j['state'],'done');self.assertEqual(received[0][0],'Bearer secret-nai')
                self.assertEqual(received[0][1]['model'],'nai-diffusion-4-5-full')
                self.assertEqual(received[0][1]['input'], 'comic style, flower')
                self.assertEqual(received[0][1]['parameters']['negative_prompt'], 'watermark')
                await upstream.close()
                response=await client.get('/api/images/'+j['id']+'/0')
                self.assertEqual(response.status,200);self.assertTrue((await response.read()).startswith(b'\x89PNG'))
                self.assertNotIn('secret-nai',str(j))
