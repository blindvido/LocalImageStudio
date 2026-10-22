import asyncio
import base64
import io
import tempfile
import unittest
from PIL import Image
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from studio_server import create_app


def png(size=(64, 48), color='white', mode='RGB'):
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, format='PNG')
    return out.getvalue()


class EditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.graphs = []
        self.mask = png(color=255, mode='L')
        self.uploaded = []
        async def upload(request):
            parts = await request.multipart()
            item = await parts.next()
            self.uploaded.append(bytes(await item.read()))
            return web.json_response({'name': 'asset.png', 'subfolder': '', 'type': 'input'})
        async def submit(request):
            self.graphs.append((await request.json())['prompt'])
            return web.json_response({'prompt_id': str(len(self.graphs))})
        async def history(request):
            return web.json_response({request.match_info['id']: {'status': {'completed': True}, 'outputs': {'9': {'images': [{'filename': 'mask.png', 'type': 'output', 'subfolder': ''}]}}}})
        async def view(request):
            return web.Response(body=self.mask, content_type='image/png')
        async def completion(request):
            self.prompt_body = await request.json()
            return web.json_response({'choices': [{'message': {'content': '{"positive_prompt":"blue whale earrings", "negative_prompt":"blurry"}'}}]})
        fake = web.Application()
        fake.router.add_post('/upload/image', upload)
        fake.router.add_post('/prompt', submit)
        fake.router.add_get('/history/{id}', history)
        fake.router.add_get('/view', view)
        fake.router.add_post('/v1/chat/completions', completion)
        self.comfy = TestServer(fake); await self.comfy.start_server()
        self.client = TestClient(TestServer(create_app(self.directory.name))); await self.client.start_server()
        self.headers = {'X-Studio-Token': (await (await self.client.get('/api/bootstrap')).json())['token']}
        await self.client.put('/api/config', headers=self.headers, json={'comfy_url': str(self.comfy.make_url('')).rstrip('/'), 'api_url': str(self.comfy.make_url('/v1')), 'model': 'vision'})

    async def asyncTearDown(self):
        await self.client.close(); await self.comfy.close(); self.directory.cleanup()

    async def upload(self, content=None):
        form = FormData(); form.add_field('image', content if content is not None else png(), filename='original.png', content_type='image/png')
        return await self.client.post('/api/edit/upload', data=form, headers=self.headers)

    async def select(self, edit, coords=None):
        response = await self.client.post('/api/edit/select', headers=self.headers, json={'edit_id': edit['id'], 'region': 'earrings', 'positive_coords': coords or [{'x': 10, 'y': 20}], 'threshold': .4})
        self.assertEqual(response.status, 200, await response.text())
        job = await response.json()
        for _ in range(30):
            job = await (await self.client.get('/api/jobs/' + job['id'])).json()
            if job['state'] in {'done', 'failed'}: break
            await asyncio.sleep(.02)
        self.assertEqual(job['state'], 'done')
        return job

    async def test_upload_decodes_image_and_rejects_non_image(self):
        response = await self.upload()
        self.assertEqual(response.status, 200, 'image-edit upload endpoint must exist')
        edit = await response.json()
        self.assertEqual((edit['width'], edit['height']), (64, 48))
        self.assertEqual((await self.upload(b'not an image')).status, 400)

    async def test_selection_uses_sam_and_source_points(self):
        edit = await (await self.upload()).json(); await self.select(edit)
        graph = self.graphs[-1]
        detect = next(n['inputs'] for n in graph.values() if n['class_type'] == 'SAM3_Detect')
        self.assertNotIn('positive_coords', detect, 'text candidates must be filtered by points rather than unioned with tracker masks')
        self.assertEqual(detect['threshold'], .4)
        self.assertEqual(graph['3']['inputs']['text'], 'earrings:10', 'detect multiple candidates before point filtering')
        self.assertFalse(any(n['class_type'] == 'DetailerForEach' for n in graph.values()))

    async def test_float_integer_coordinates_are_normalized_before_mask_filtering(self):
        edit = await (await self.upload()).json()
        job = await self.select(edit, [{'x':10.0,'y':20.0}])
        self.assertIsInstance(job['selection_settings']['positive_coords'][0]['x'], int)

    async def test_render_requires_confirmation_and_latest_valid_mask(self):
        edit = await (await self.upload()).json(); job = await self.select(edit)
        body = {'edit_id': edit['id'], 'mask_job_id': job['id'], 'positive_prompt': 'blue whale earrings'}
        self.assertEqual((await self.client.post('/api/edit/render', json=body, headers=self.headers)).status, 400)
        body['confirmed'] = True
        response = await self.client.post('/api/edit/render', json=body, headers=self.headers)
        self.assertEqual(response.status, 200, await response.text())
        await asyncio.sleep(.08)
        graph = self.graphs[-1]
        loader = next(n for n in graph.values() if n['class_type'] == 'CheckpointLoaderSimple')
        self.assertEqual(loader['inputs']['ckpt_name'], 'waiIllustriousSDXL_v170.safetensors')
        self.assertTrue(any(n['class_type'] == 'DetailerForEach' for n in graph.values()))
        await self.select(edit)
        self.assertEqual((await self.client.post('/api/edit/render', json=body, headers=self.headers)).status, 400)

    async def test_empty_or_mismatched_selection_fails_before_confirmation(self):
        edit = await (await self.upload()).json()
        for mask in [png(color=0, mode='L'), png(size=(10,10), color=255, mode='L')]:
            self.mask = mask
            job = await (await self.client.post('/api/edit/select', headers=self.headers, json={'edit_id': edit['id'], 'region':'face'})).json()
            for _ in range(30):
                job = await (await self.client.get('/api/jobs/' + job['id'])).json()
                if job['state'] in {'done','failed'}: break
                await asyncio.sleep(.02)
            self.assertEqual(job['state'],'failed')
            self.assertTrue(job['error'])

    async def test_include_exclude_filters_text_candidate_regions(self):
        mask = Image.new('L',(64,48),0)
        for x in range(5,25):
            for y in range(10,35): mask.putpixel((x,y),255)
        for x in range(40,60):
            for y in range(10,35): mask.putpixel((x,y),255)
        out=io.BytesIO();mask.save(out,format='PNG');self.mask=out.getvalue()
        edit = await (await self.upload()).json()
        job = await (await self.client.post('/api/edit/select', headers=self.headers, json={'edit_id':edit['id'],'region':'face','positive_coords':[{'x':10,'y':20}],'negative_coords':[{'x':50,'y':20}]})).json()
        for _ in range(30):
            job=await (await self.client.get('/api/jobs/'+job['id'])).json()
            if job['state'] in {'done','failed'}:break
            await asyncio.sleep(.02)
        self.assertEqual(job['state'],'done')
        result=await self.client.get('/api/images/'+job['id']+'/0')
        image=Image.open(io.BytesIO(await result.read())).convert('L')
        self.assertEqual(image.getpixel((10,20)),255)
        self.assertEqual(image.getpixel((50,20)),0,'excluded face must not enter final mask')

    async def test_prompt_includes_original_and_preservation_instructions(self):
        edit = await (await self.upload()).json()
        response = await self.client.post('/api/edit/prompt', headers=self.headers, json={'edit_id': edit['id'], 'description': 'change earrings', 'preserve': 'blue hair'})
        self.assertEqual(response.status, 200, await response.text())
        content = self.prompt_body['messages'][1]['content']
        self.assertIn('blue hair', content[0]['text'])
        self.assertTrue(content[1]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertEqual(base64.b64decode(content[1]['image_url']['url'].split(',')[1]), png())

    async def test_character_library_is_injected_into_edit_prompt(self):
        response = await self.client.post('/api/characters', headers=self.headers, json={'name':'DS','appearance':'blue hair, whale hair clip, fish-fin ears','clothing':'maid outfit'})
        self.assertEqual(response.status, 200)
        edit = await (await self.upload()).json()
        response = await self.client.post('/api/edit/prompt', headers=self.headers, json={'edit_id':edit['id'],'description':'DS换成开心的表情'})
        result = await response.json()
        self.assertEqual(result['matched_characters'][0]['name'], 'DS')
        self.assertIn('whale hair clip', self.prompt_body['messages'][0]['content'])


if __name__ == '__main__': unittest.main()
