import asyncio
import copy
import json
import tempfile
import unittest
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient
from studio_server import create_app, STATE
from test_novelai import image_zip
from test_nai_panels import prompt_data


class PanelAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.received = []
        self.prompt_received = []
        self.invalid_draft = False
        async def generate(request):
            self.received.append(await request.json())
            await asyncio.sleep(.02)
            return web.Response(body=image_zip(), content_type='application/zip')
        async def draft(request):
            self.prompt_received.append(await request.json())
            result = copy.deepcopy(prompt_data())
            result['characters'][0]['character_id'] = self.belle['id']
            result['characters'][1]['character_id'] = self.wise['id']
            result['characters'][0]['positive'] = 'model rewrote the fixed character anchor'
            result['characters'][0]['direction'] = 'turning toward screen right, raised eyebrow'
            if self.invalid_draft: result['characters'] = []
            return web.json_response({'choices': [{'message': {'content': json.dumps(result)}}]})
        fake = web.Application(); fake.router.add_post('/ai/generate-image', generate); fake.router.add_post('/v1/chat/completions', draft)
        self.upstream = TestServer(fake); await self.upstream.start_server()
        self.client = TestClient(TestServer(create_app(self.folder.name))); await self.client.start_server()
        self.headers = {'X-Studio-Token': (await (await self.client.get('/api/bootstrap')).json())['token']}
        await self.call('PUT', '/api/config', {'novelai_url': str(self.upstream.make_url('')).rstrip('/'), 'novelai_token': 'secret-nai', 'novelai_model': 'nai-diffusion-4-5-full', 'api_url': str(self.upstream.make_url('/v1')), 'api_key': 'secret-prompt', 'model': 'test'})
        self.belle = await self.call('POST', '/api/characters', {'name': '玲', 'appearance': 'girl, blue hair', 'notes': 'SECRET_FUTURE'})
        self.wise = await self.call('POST', '/api/characters', {'name': '哲', 'appearance': 'boy, silver hair'})
        self.body = {'project': '坠落', 'chapter': '01', 'page': '02', 'panel': '06', 'visible_description': '两人在交谈', 'private_notes': 'SECRET_FUTURE', 'nai_prompt': prompt_data(), 'values': {'MODEL_NAME': 'nai-diffusion-4-5-full', 'seed': 77}}
        self.body['nai_prompt']['characters'][0]['character_id'] = self.belle['id']
        self.body['nai_prompt']['characters'][1]['character_id'] = self.wise['id']

    async def asyncTearDown(self):
        await self.client.close(); await self.upstream.close(); self.folder.cleanup()

    async def call(self, method, path, data=None, status=200):
        response = await self.client.request(method, path, json=data, headers=self.headers)
        result = await response.json()
        self.assertEqual(response.status, status, result)
        return result

    async def wait_done(self, job):
        for _ in range(40):
            job = await self.call('GET', '/api/jobs/' + job['id'])
            if job['state'] in {'done', 'failed', 'unknown'}: break
            await asyncio.sleep(.02)
        self.assertEqual(job['state'], 'done', job)
        return job

    async def test_create_reload_revision_and_duplicate_labels(self):
        a = await self.call('POST', '/api/panels', self.body)
        b = await self.call('POST', '/api/panels', self.body)
        self.assertNotEqual(a['id'], b['id'])
        self.assertEqual(a, await self.call('GET', '/api/panels/' + a['id']))
        edited = await self.call('PUT', '/api/panels/' + a['id'], {**a, 'private_notes': 'changed'})
        self.assertEqual(edited['revision'], 2)
        await self.call('PUT', '/api/panels/' + a['id'], a, status=409)
        from storage import Store
        self.assertEqual(Store(self.folder.name).panels[a['id']]['revision'], 2)

    async def test_overlapping_updates_cannot_both_accept_same_revision(self):
        panel = await self.call('POST', '/api/panels', self.body)
        first_chunk, release = asyncio.Event(), asyncio.Event()
        encoded = json.dumps({**panel, 'private_notes': 'slow writer'}).encode()
        async def slow_body():
            yield encoded[:1]
            first_chunk.set()
            await release.wait()
            yield encoded[1:]
        slow = asyncio.create_task(self.client.put('/api/panels/' + panel['id'], data=slow_body(), headers={**self.headers, 'Content-Type': 'application/json'}))
        await first_chunk.wait(); await asyncio.sleep(.05)
        fast = await self.call('PUT', '/api/panels/' + panel['id'], {**panel, 'private_notes': 'fast writer'})
        self.assertEqual(fast['revision'], 2)
        release.set(); response = await slow
        self.assertEqual(response.status, 409, await response.text())
        self.assertEqual((await self.call('GET', '/api/panels/' + panel['id']))['private_notes'], 'fast writer')

    async def test_character_delete_preserves_snapshot(self):
        panel = await self.call('POST', '/api/panels', self.body)
        await self.call('DELETE', '/api/characters/' + self.belle['id'])
        saved = await self.call('PUT', '/api/panels/' + panel['id'], panel)
        self.assertEqual(saved['character_snapshots'][0]['appearance'], 'girl, blue hair')
        self.assertNotIn('SECRET_FUTURE', str(saved['character_snapshots']))

    async def test_restore_historical_character_after_removed_and_deleted(self):
        panel = await self.call('POST', '/api/panels', self.body)
        job = await self.wait_done(await self.call('POST', '/api/jobs', {'provider': 'novelai', 'panel_id': panel['id'], 'panel_revision': 1, 'request_id': 'historical', 'use_api': False}))
        newer = await self.call('PUT', '/api/panels/' + panel['id'], {**panel, 'nai_prompt': {**panel['nai_prompt'], 'characters': panel['nai_prompt']['characters'][1:]}})
        await self.call('DELETE', '/api/characters/' + self.belle['id'])
        restored = await self.call('PUT', '/api/panels/' + panel['id'], {'revision': newer['revision'], 'restore_job_id': job['id']})
        self.assertEqual(len(restored['nai_prompt']['characters']), 2)
        self.assertEqual(restored['character_snapshots'][0]['appearance'], 'girl, blue hair')
        self.assertEqual(restored['revision'], 3)
        await self.call('PUT', '/api/panels/' + panel['id'], restored)

    async def test_preview_and_draft_no_private_information(self):
        panel = await self.call('POST', '/api/panels', self.body)
        preview = await self.call('POST', '/api/nai/preview', {'panel_id': panel['id'], 'panel_revision': 1})
        self.assertEqual(preview['parameters']['seed'], 77)
        self.assertEqual(len(preview['parameters']['v4_prompt']['caption']['char_captions']), 2)
        self.assertNotIn('SECRET_FUTURE', str(preview))
        draft = await self.call('POST', '/api/nai/prompt-draft', {**self.body, 'private_notes': 'SECRET_FUTURE'})
        self.assertEqual(draft['characters'][0]['character_id'], self.belle['id'])
        self.assertNotIn('SECRET_FUTURE', str(self.prompt_received))

    async def test_prompt_draft_keeps_fixed_anchor_and_returns_per_panel_direction(self):
        request = copy.deepcopy(self.body)
        request['nai_prompt']['characters'][0]['positive'] = 'fixed Belle hair, eyes, clip, jacket and skirt anchor'
        request['nai_prompt']['characters'][0]['negative'] = 'silver hair'
        request['nai_prompt']['characters'][0]['center'] = {'x': .42, 'y': .6}
        draft = await self.call('POST', '/api/nai/prompt-draft', request)
        self.assertEqual(draft['characters'][0]['positive'], 'fixed Belle hair, eyes, clip, jacket and skirt anchor')
        self.assertEqual(draft['characters'][0]['negative'], 'silver hair')
        self.assertEqual(draft['characters'][0]['direction'], 'turning toward screen right, raised eyebrow')
        self.assertEqual(draft['characters'][0]['center'], {'x': .42, 'y': .6})

    async def test_dedup_frozen_attempt_and_select(self):
        panel = await self.call('POST', '/api/panels', self.body)
        req = {'provider': 'novelai', 'panel_id': panel['id'], 'panel_revision': 1, 'request_id': 'test-delivery', 'random_seed': False, 'use_api': False}
        first = await self.call('POST', '/api/jobs', req)
        second = await self.call('POST', '/api/jobs', req)
        self.assertEqual(first['id'], second['id'])
        job = await self.wait_done(first)
        self.assertEqual(len(self.received), 1)
        self.assertEqual(job['request_snapshot'], self.received[0])
        self.assertNotIn('SECRET_FUTURE', str(job)); self.assertNotIn('secret-nai', str(job))
        self.assertNotIn('secret-prompt', str(job))
        newer = await self.call('PUT', '/api/panels/' + panel['id'], {**panel, 'nai_prompt': {**panel['nai_prompt'], 'base_positive': 'new scene'}})
        self.assertEqual((await self.call('POST', '/api/jobs', req))['id'], first['id'])
        await self.call('POST', '/api/jobs', {**req, 'random_seed': True}, status=409)
        chosen = await self.call('PUT', '/api/panels/' + panel['id'], {**newer, 'selected_job_id': job['id']})
        self.assertEqual(chosen['selected_job_id'], job['id'])
        await self.call('DELETE', '/api/jobs/' + job['id'], status=400)

    async def test_stale_panel_and_parent_from_another_panel_rejected(self):
        a = await self.call('POST', '/api/panels', self.body)
        b = await self.call('POST', '/api/panels', self.body)
        req = {'provider': 'novelai', 'panel_id': a['id'], 'panel_revision': 1, 'request_id': 'a', 'use_api': False}
        first = await self.wait_done(await self.call('POST', '/api/jobs', req))
        await self.call('POST', '/api/jobs', {**req, 'panel_id': b['id'], 'request_id': 'b', 'parent_job_id': first['id']}, status=400)
        await self.call('POST', '/api/jobs', {**req, 'request_id': 'new', 'panel_revision': 9}, status=409)
        self.assertEqual(len(self.received), 1)

    async def test_restart_keeps_unknown_job_without_resubmit(self):
        store = self.client.server.app[STATE].store
        store.jobs['unconfirmed'] = {'id': 'unconfirmed', 'state': 'submitting', 'provider': 'novelai', 'images': [], 'error': ''}
        store.save('jobs')
        await self.client.close()
        self.client = TestClient(TestServer(create_app(self.folder.name))); await self.client.start_server()
        self.headers = {'X-Studio-Token': (await (await self.client.get('/api/bootstrap')).json())['token']}
        job = await self.call('GET', '/api/jobs/unconfirmed')
        self.assertEqual(job['state'], 'unknown'); self.assertEqual(self.received, [])
        await self.call('POST', '/api/jobs/unconfirmed/resume', {}, status=400)

    async def test_invalid_draft_does_not_edit_panel_or_generate_image(self):
        panel = await self.call('POST', '/api/panels', self.body)
        self.invalid_draft = True
        await self.call('POST', '/api/nai/prompt-draft', self.body, status=400)
        self.assertEqual(await self.call('GET', '/api/panels/' + panel['id']), panel)
        self.assertEqual(self.received, [])

    async def test_legacy_reference_and_panel_overrides_are_rejected(self):
        await self.call('POST', '/api/jobs', {'provider': 'novelai', 'positive_prompt': 'girl', 'reference_image': 'data'}, status=400)
        panel = await self.call('POST', '/api/panels', self.body)
        await self.call('POST', '/api/jobs', {'provider': 'novelai', 'panel_id': panel['id'], 'panel_revision': 1, 'request_id': 'override', 'values': {'seed': 5}}, status=400)
        self.assertEqual(self.received, [])
