"""Local image studio. No changes to ComfyUI or SillyTavern are required."""
import sys
from pathlib import Path

# ComfyUI portable Python uses an isolated ._pth file.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import asyncio
import base64
import copy
from datetime import datetime
import json
import logging
import secrets
import subprocess
import time
import uuid
from urllib.parse import urlsplit
import aiohttp
import novelai
import nai_routes
import nai_redraw
import prompt_presets
import comic_review
import comic_editor
import nai_chat
import history_tags
from aiohttp import web
from clients import ComfyClient, SubmissionUnconfirmed, base_url, generate_prompt, list_models
from storage import Store, DEFAULT_VALUES
from network import SystemProxySession
from characters import clean_record, match_characters, character_template
from workflow import inspect_graph, render_graph
from editing import EditAssets, MAX_BYTES, check_mask, filter_mask, selection_graph, redraw_graph, prompt_image, number, points
from novelai_limits import NAIGenerationLimiter

PORTABLE = ROOT.parent
WORKFLOW_SOURCE = PORTABLE / 'ComfyUI' / 'user' / 'default' / 'workflows'
STATE = web.AppKey('studio_state', object)
FINAL_STATES = {'done', 'failed', 'unknown', 'interrupted'}


def starter_graph():
    return {
        '1': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': '%MODEL_NAME%'}},
        '2': {'class_type': 'CLIPTextEncode', 'inputs': {'text': '%prompt%', 'clip': ['1', 1]}, '_meta': {'title': '正面提示词'}},
        '3': {'class_type': 'CLIPTextEncode', 'inputs': {'text': '%negative_prompt%', 'clip': ['1', 1]}, '_meta': {'title': '负面提示词'}},
        '4': {'class_type': 'EmptyLatentImage', 'inputs': {'width': '%width%', 'height': '%height%', 'batch_size': 1}},
        '5': {'class_type': 'KSampler', 'inputs': {'model': ['1', 0], 'positive': ['2', 0], 'negative': ['3', 0], 'latent_image': ['4', 0], 'seed': '%seed%', 'steps': '%steps%', 'cfg': '%cfg_scale%', 'sampler_name': '%sampler_name%', 'scheduler': '%scheduler%', 'denoise': 1.0}},
        '6': {'class_type': 'VAEDecode', 'inputs': {'samples': ['5', 0], 'vae': ['1', 2]}},
        '7': {'class_type': 'SaveImage', 'inputs': {'images': ['6', 0], 'filename_prefix': 'LocalImageStudio'}},
    }


def new_workflow(name, graph):
    inspect_graph(graph)
    return {'id': str(uuid.uuid4()), 'name': name.strip() or '未命名工作流', 'graph': graph,
            'values': copy.deepcopy(DEFAULT_VALUES), 'mappings': {}, 'template': 'tags',
            'fixed_positive': '', 'fixed_negative': '', 'random_seed': True}


class Studio:
    def __init__(self, data_dir):
        self.store = Store(data_dir)
        self.token = secrets.token_urlsafe(32)
        self.session = None
        self.tasks = set()
        self.launch_lock = asyncio.Lock()
        self.edit_lock = asyncio.Lock()
        self.novelai_lock = asyncio.Lock()
        self.novelai_limiter = NAIGenerationLimiter(self.store.directory / 'novelai-usage.json')
        self.novelai_limiter.import_history_once(self.store.jobs)
        self.edits = EditAssets(data_dir)
        if not self.store.workflows:
            workflow = new_workflow('基础文生图 · SDXL / SD', starter_graph())
            workflow['values']['MODEL_NAME'] = 'waiIllustriousSDXL_v170.safetensors'
            workflow['values']['sampler_name'] = 'euler'
            self.store.workflows[workflow['id']] = workflow
            self.store.save('workflows')

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def persist_job(self, job):
        job['updated_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
        self.store.jobs[job['id']] = job
        self.store.save('jobs')

    def sanitize_error(self, error, key=None):
        text = str(error)
        for secret in {key, self.store.config.get('api_key'), self.store.config.get('novelai_token')} - {None, ''}:
            text = text.replace(secret, '[已隐藏]')
        return text[:2500]

    async def run_job(self, job, config):
        try:
            workflow = job['workflow']
            if job['use_api']:
                template = config['templates'].get(workflow['template'])
                if not template:
                    raise ValueError('所选提示词模板不存在，请重新选择。')
                prompts = await generate_prompt(self.session, config, job['description'], character_template(template['text'], job.get('matched_characters', [])))
                job['positive_prompt'] = ', '.join(x for x in [workflow['fixed_positive'].strip(), prompts['positive_prompt']] if x)
                job['negative_prompt'] = ', '.join(x for x in [workflow['fixed_negative'].strip(), prompts['negative_prompt']] if x)
            if not job['positive_prompt'].strip():
                raise ValueError('正面提示词为空，请先生成或手动填写。')
            if job.get('provider') == 'novelai':
                async with self.novelai_lock:
                    job['state'] = 'waiting_rate_limit'
                    self.persist_job(job)
                    job['rate_limit'] = await self.novelai_limiter.reserve()
                    job['state'] = 'submitting'
                    self.persist_job(job)
                    try:
                        redraw = job.get('img2img')
                        extra = {}
                        if redraw:
                            source = (self.store.directory / 'generated' / 'novelai' / job['id'] / 'source.png').read_bytes()
                            extra['img2img'] = {'image': source, 'strength': redraw['strength']}
                        content = await novelai.generate(self.session, config, job['positive_prompt'], job['negative_prompt'], job['values'], characters=job.get('nai_prompt', {}).get('characters'), **extra)
                    finally:
                        self.novelai_limiter.finished()
                    folder = self.store.directory / 'generated' / 'novelai' / job['id']
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / '0.png').write_bytes(content)
                    job.update(state='done',images=[{'filename':'0.png','type':'studio','subfolder':''}],error='')
                    self.persist_job(job)
                return
            values = {**job['values'], 'prompt': job['positive_prompt'], 'negative_prompt': job['negative_prompt']}
            graph = render_graph(workflow['graph'], values, workflow['mappings'])
            job['state'] = 'submitting'
            self.persist_job(job)
            client = ComfyClient(self.session, job['comfy_url'], job['client_id'])
            job['prompt_id'] = await client.submit(graph)
            job['state'] = 'queued'
            self.persist_job(job)
            await self.monitor(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            job['state'] = 'unknown' if isinstance(exc, SubmissionUnconfirmed) else 'failed'
            job['error'] = self.sanitize_error(exc, config.get('api_key'))
            self.persist_job(job)

    async def events(self, job):
        url = job['comfy_url'].replace('http://', 'ws://', 1).replace('https://', 'wss://', 1)
        try:
            async with self.session.ws_connect(url + '/ws', params={'clientId': job['client_id']}, heartbeat=20) as socket:
                async for message in socket:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        continue
                    event = json.loads(message.data)
                    data = event.get('data', {})
                    if data.get('prompt_id') != job.get('prompt_id'):
                        continue
                    if event.get('type') == 'progress' and data.get('max'):
                        job['state'] = 'running'
                        job['progress'] = {'value': data['value'], 'max': data['max']}
                        self.persist_job(job)
                    elif event.get('type') in {'executing', 'execution_start'}:
                        job['state'] = 'running'
                        job.pop('progress', None)
                        self.persist_job(job)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            pass  # History polling remains authoritative.

    async def monitor(self, job):
        client = ComfyClient(self.session, job['comfy_url'], job['client_id'])
        events = asyncio.create_task(self.events(job))
        started, last_present = time.monotonic(), time.monotonic()
        try:
            while time.monotonic() - started < 3600:
                history = None
                try:
                    history = await client.history(job['prompt_id'])
                    if history:
                        images = client.images(history)
                        status = history.get('status', {})
                        if status.get('completed') or status.get('status_str') == 'success':
                            if not images:
                                raise ValueError('工作流已结束，但没有图片输出。请使用 SaveImage 或 PreviewImage 节点。')
                            if job.get('edit_purpose') == 'selection':
                                await self.finish_selection(job, images)
                            job.update(state='done', images=images, error='')
                            job.pop('notice', None)
                            self.persist_job(job)
                            return
                    queue = await client.request('GET', '/queue')
                    running = any(row[1] == job['prompt_id'] for row in queue.get('queue_running', []))
                    pending = any(row[1] == job['prompt_id'] for row in queue.get('queue_pending', []))
                    if running or pending or history:
                        last_present = time.monotonic()
                    if running and job['state'] != 'running':
                        job['state'] = 'running'
                        self.persist_job(job)
                    if not history and not running and not pending and time.monotonic() - last_present > 30:
                        job.update(state='unknown', error='任务已不在 ComfyUI 队列和历史中，状态无法恢复。可点击继续查询；未重新提交。')
                        self.persist_job(job)
                        return
                    if job.pop('notice', None):
                        self.persist_job(job)
                except ValueError as exc:
                    if history is not None and history.get('status', {}).get('status_str') == 'error':
                        raise
                    if '没有图片输出' in str(exc) or (history and history.get('status', {}).get('completed') and job.get('edit_purpose') == 'selection'):
                        raise
                    job['notice'] = self.sanitize_error(exc)
                    self.persist_job(job)
                await asyncio.sleep(2)
            job.update(state='unknown', error='等待超过一小时，可点击继续查询已有任务；未重新提交。')
            self.persist_job(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            job.update(state='failed', error=self.sanitize_error(exc))
            self.persist_job(job)
        finally:
            events.cancel()
            await asyncio.gather(events, return_exceptions=True)

    async def finish_selection(self, job, images):
        edit = self.edits.get(job['edit_id'])
        params = {k: images[0].get(k, '') for k in ('filename', 'subfolder', 'type')}
        async with self.session.get(job['comfy_url'] + '/view', params=params, timeout=aiohttp.ClientTimeout(total=60)) as response:
            if response.status != 200: raise ValueError('选区图片无法读取，请检查 ComfyUI。')
            content = filter_mask(await response.read(), (edit['width'], edit['height']), job['selection_settings'])
        path = self.edits.folder(edit['id']) / ('mask-' + job['id'] + '.png')
        path.write_bytes(content)
        job['mask_ready'] = True


@web.middleware
async def guard(request, handler):
    if request.host.split(':')[0] not in {'127.0.0.1', 'localhost'}:
        return web.json_response({'error': '仅允许本机访问。'}, status=403)
    origin = request.headers.get('Origin')
    if origin and origin != 'http://' + request.host:
        return web.json_response({'error': '拒绝跨来源访问。'}, status=403)
    if request.headers.get('Sec-Fetch-Site') == 'cross-site':
        return web.json_response({'error': '拒绝跨站访问。'}, status=403)
    studio = request.app[STATE]
    if request.method not in {'GET', 'HEAD'} and not secrets.compare_digest(request.headers.get('X-Studio-Token', ''), studio.token):
        return web.json_response({'error': '访问凭证失效，请刷新页面。'}, status=403)
    try:
        response = await handler(request)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        response = web.json_response({'error': studio.sanitize_error(exc)}, status=400)
    except web.HTTPException:
        raise
    except Exception:
        logging.exception('Local studio request failed')
        response = web.json_response({'error': '本地服务处理失败，请检查软件日志。'}, status=500)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def workflow_public(workflow):
    return {**workflow, 'inspection': inspect_graph(workflow['graph'])}


async def bootstrap(request):
    studio = request.app[STATE]
    return web.json_response({'token': studio.token, 'config': studio.store.public_config(), 'version': '1.0.0'})


async def config(request):
    store = request.app[STATE].store
    if request.method == 'GET':
        return web.json_response(store.public_config())
    data = await request.json()
    candidate = copy.deepcopy(store.config)
    for key in ['api_url', 'model', 'comfy_url', 'api_key', 'api_timeout', 'templates', 'novelai_url','novelai_token','novelai_model','image_provider','novelai_fixed_positive','novelai_fixed_negative']:
        if key in data:
            candidate[key] = data[key]
    for key in ('novelai_fixed_positive', 'novelai_fixed_negative'):
        if not isinstance(candidate[key], str) or len(candidate[key]) > 20000:
            raise ValueError('NovelAI 全局提示词须为不超过 20000 字符的文本。')
    candidate['comfy_url'] = base_url(candidate['comfy_url'])
    candidate['novelai_url'] = base_url(candidate['novelai_url'])
    if not isinstance(candidate['novelai_token'],str) or candidate['novelai_model'] not in novelai.MODELS or candidate['image_provider'] not in {'comfyui','novelai'}:
        raise ValueError('NovelAI Token、模型或生图来源无效。')
    if candidate['novelai_token'].startswith('Bearer '): candidate['novelai_token'] = candidate['novelai_token'][7:].strip()
    if candidate['api_url']:
        candidate['api_url'] = base_url(candidate['api_url'])
    if not isinstance(candidate['api_key'], str) or not isinstance(candidate['model'], str):
        raise ValueError('密钥和模型名称须为文本。')
    if not 5 <= float(candidate['api_timeout']) <= 600:
        raise ValueError('API 超时须为 5–600 秒。')
    templates = candidate['templates']
    if not isinstance(templates, dict) or not templates or any(not isinstance(t, dict) or not isinstance(t.get('name'), str) or not isinstance(t.get('text'), str) or not t['text'].strip() for t in templates.values()):
        raise ValueError('至少保留一个包含名称和系统提示词的模板。')
    store.config = candidate
    store.save('config')
    return web.json_response(store.public_config())


async def novelai_test(request):
    studio = request.app[STATE]
    return web.json_response(await novelai.test_connection(studio.session, studio.store.config))


async def models(request):
    studio = request.app[STATE]
    data = await request.json()
    candidate = copy.deepcopy(studio.store.config)
    for key in ('api_url', 'api_key'):
        if key in data:
            if not isinstance(data[key], str):
                raise ValueError('地址和密钥须为文本。')
            candidate[key] = data[key].strip()
    if candidate['api_key'] and base_url(candidate['api_url']) != base_url(studio.store.config['api_url'] or candidate['api_url']) and 'api_key' not in data:
        raise ValueError('更换服务地址后，请重新填写该连接的密钥。')
    result = await list_models(studio.session, candidate)
    return web.json_response({'models': result, 'api_url': candidate['api_url']})


async def workflows(request):
    store = request.app[STATE].store
    if request.method == 'GET':
        return web.json_response([workflow_public(w) for w in store.workflows.values()])
    data = await request.json()
    graph = data.get('graph')
    if 'source' in data:
        source = data['source']
        if not isinstance(source, str) or Path(source).name != source or not source.endswith('.json'):
            raise ValueError('无效的工作流文件名。')
        path = (WORKFLOW_SOURCE / source).resolve()
        if path.parent != WORKFLOW_SOURCE.resolve() or not path.is_file():
            raise ValueError('工作流文件不存在。')
        graph = json.loads(path.read_text(encoding='utf-8-sig'))
    if isinstance(graph, str):
        graph = json.loads(graph)
    workflow = new_workflow(data.get('name', '导入工作流'), graph)
    store.workflows[workflow['id']] = workflow
    store.save('workflows')
    return web.json_response(workflow_public(workflow))


async def workflow_item(request):
    store = request.app[STATE].store
    key = request.match_info['id']
    if key not in store.workflows:
        raise web.HTTPNotFound()
    if request.method == 'DELETE':
        del store.workflows[key]
        store.save('workflows')
        return web.json_response({'ok': True})
    data = await request.json()
    candidate = copy.deepcopy(store.workflows[key])
    for field in ['name', 'graph', 'values', 'mappings', 'template', 'fixed_positive', 'fixed_negative', 'random_seed']:
        if field in data:
            candidate[field] = data[field]
    candidate['name'] = str(candidate['name']).strip()
    if not candidate['name']:
        raise ValueError('工作流名称不能为空。')
    # Validate graph/mappings before committing the update. Empty model variables
    # are allowed in saved presets, but never in an actual submitted graph.
    info = inspect_graph(candidate['graph'])
    probe = {name: ('1' if name not in {'width', 'height', 'steps'} else 64) for name in info['variables']}
    probe.update({name: 1 for name in candidate['mappings']})
    render_graph(candidate['graph'], probe, candidate['mappings'])
    store.workflows[key] = candidate
    store.save('workflows')
    return web.json_response(workflow_public(candidate))


async def sources(request):
    return web.json_response([path.name for path in sorted(WORKFLOW_SOURCE.glob('*.json'))])


async def prompt(request):
    studio = request.app[STATE]
    data = await request.json()
    workflow = {'template':'tags', **novelai.fixed_prompts(studio.store.config)} if data.get('provider')=='novelai' else studio.store.workflows[data['workflow_id']]
    template = studio.store.config['templates'].get(data.get('template', workflow['template']))
    if not template:
        raise ValueError('请选择有效的提示词模板。')
    matched = match_characters(studio.store.characters, data.get('description', ''))
    result = await generate_prompt(studio.session, studio.store.config, data.get('description', ''), character_template(template['text'], matched))
    return web.json_response({
        'matched_characters': matched,
        'positive_prompt': ', '.join(x for x in [workflow['fixed_positive'].strip(), result['positive_prompt']] if x),
        'negative_prompt': ', '.join(x for x in [workflow['fixed_negative'].strip(), result['negative_prompt']] if x)})


async def jobs(request):
    studio = request.app[STATE]
    if request.method == 'GET':
        return web.json_response([history_tags.tagged(j) for j in sorted(studio.store.jobs.values(), key=lambda j: j['created_at'], reverse=True)])
    data = await request.json()
    if data.get('provider') == 'novelai':
        if any('reference' in k.lower() or k in {'image', 'strength', 'noise', 'action'} for k in data):
            raise ValueError('此入口暂不支持参考图条件或图生图；参考图仅供查看。')
        if any(key in data for key in ('nai_prompt', 'panel_id', 'request_id')):
            return await nai_routes.submit(studio, data)
        if not studio.store.config.get('novelai_token'):
            raise ValueError('请先在连接与设置填写 NovelAI Token。')
        values = {**novelai.DEFAULTS, 'MODEL_NAME':studio.store.config['novelai_model'], **data.get('values',{})}
        if data.get('random_seed',True): values['seed'] = secrets.randbelow(2**32)
        novelai.payload(data.get('positive_prompt') or 'pending',data.get('negative_prompt',''),values)
        use_api = bool(data.get('use_api',False))
        description = data.get('description','')
        if use_api and not description.strip():raise ValueError('请先描述画面。')
        if not use_api and not data.get('positive_prompt','').strip():raise ValueError('请填写正面提示词。')
        template = data.get('template','tags')
        if template not in studio.store.config['templates']:raise ValueError('提示词模板不存在。')
        workflow = {'id':'novelai','name':'NovelAI · '+values['MODEL_NAME'],'graph':{},'mappings':{},'values':values,'template':template, **novelai.fixed_prompts(studio.store.config)}
        job = {'id':str(uuid.uuid4()),'client_id':str(uuid.uuid4()),'provider':'novelai','workflow':workflow,'workflow_id':'novelai','values':values,
               'comfy_url':'','created_at':datetime.now().astimezone().isoformat(timespec='seconds'), 'description':description,
               'positive_prompt':data.get('positive_prompt',''),'negative_prompt':data.get('negative_prompt',''),'use_api':use_api,
               'state':'generating_prompt' if use_api else 'preparing','images':[],'error':'',
               'matched_characters':match_characters(studio.store.characters,description) if use_api else []}
        if not use_api:
            job['positive_prompt'], job['negative_prompt'] = novelai.apply_fixed(studio.store.config, job['positive_prompt'], job['negative_prompt'])
        studio.persist_job(job)
        studio.spawn(studio.run_job(job,copy.deepcopy(studio.store.config)))
        return web.json_response(job)
    if data.get('provider','comfyui') != 'comfyui':raise ValueError('无效的生图来源。')
    workflow = copy.deepcopy(studio.store.workflows[data['workflow_id']])
    if 'prompt' not in inspect_graph(workflow['graph'])['variables'] and 'prompt' not in workflow['mappings']:
        raise ValueError('工作流没有 %prompt% 变量或正面提示词映射，请在工作流配置中绑定 Prompt 节点。')
    workflow['template'] = data.get('template', workflow['template'])
    values = {**workflow['values'], **data.get('values', {})}
    if data.get('random_seed', workflow['random_seed']):
        values['seed'] = secrets.randbelow(2**48)
    use_api = bool(data.get('use_api', False))
    if use_api and not data.get('description', '').strip():
        raise ValueError('请先描述画面。')
    # Validate before an API call could consume credits.
    render_graph(workflow['graph'], {**values, 'prompt': data.get('positive_prompt') or 'pending', 'negative_prompt': data.get('negative_prompt', '')}, workflow['mappings'])
    now = datetime.now().astimezone().isoformat(timespec='seconds')
    job = {'id': str(uuid.uuid4()), 'client_id': str(uuid.uuid4()), 'workflow': workflow,
           'workflow_id': workflow['id'], 'values': values, 'comfy_url': studio.store.config['comfy_url'],
           'created_at': now, 'description': data.get('description', ''), 'positive_prompt': data.get('positive_prompt', ''),
           'negative_prompt': data.get('negative_prompt', ''), 'use_api': use_api,
           'state': 'generating_prompt' if use_api else 'preparing', 'images': [], 'error': ''}
    job['matched_characters'] = match_characters(studio.store.characters, job['description']) if use_api else []
    studio.persist_job(job)
    studio.spawn(studio.run_job(job, copy.deepcopy(studio.store.config)))
    return web.json_response(job)


async def job_item(request):
    studio = request.app[STATE]
    job = studio.store.jobs.get(request.match_info['id'])
    if not job:
        raise web.HTTPNotFound()
    if request.method == 'DELETE':
        if any(a.get('job_id') == job['id'] and a.get('status') == 'approved' for a in studio.store.comic_review['assets'].values()):
            raise ValueError('此图片已通过审核，请先撤销通过再删除。')
        if any(j.get('parent_job_id') == job['id'] for j in studio.store.jobs.values()):
            raise ValueError('此图片已有派生版本，需保留原图记录。')
        if job.get('request_id'):
            raise ValueError('逐格生图记录用于防重复提交和结果追溯，不能删除。')
        if job['state'] not in FINAL_STATES:
            raise ValueError('任务仍在执行，暂时不能删除记录。')
        del studio.store.jobs[job['id']]
        studio.store.save('jobs')
        return web.json_response({'ok': True})
    if request.method == 'POST':
        if job['state'] != 'unknown' or not job.get('prompt_id'):
            raise ValueError('仅支持继续查询有 ComfyUI ID 的未知任务。')
        job['state'], job['error'] = 'queued', ''
        studio.persist_job(job)
        studio.spawn(studio.monitor(job))
    return web.json_response(history_tags.tagged(job))


async def comfy(request):
    studio = request.app[STATE]
    client = ComfyClient(studio.session, studio.store.config['comfy_url'])
    action = request.match_info['action']
    if action == 'status':
        stats = await client.request('GET', '/system_stats')
        return web.json_response({'connected': True, 'devices': stats.get('devices', [])})
    if action == 'options':
        return web.json_response(await client.options())
    if action != 'start' or request.method != 'POST':
        raise web.HTTPNotFound()
    parsed = urlsplit(studio.store.config['comfy_url'])
    if parsed.hostname not in {'127.0.0.1', 'localhost'} or parsed.scheme != 'http' or parsed.path:
        raise ValueError('一键启动仅支持 http://127.0.0.1:端口 或 localhost 地址。')
    async with studio.launch_lock:
        try:
            await client.request('GET', '/system_stats')
            return web.json_response({'connected': True, 'message': 'ComfyUI 已运行，直接复用。'})
        except ValueError:
            pass
        log_dir = ROOT / 'data' / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / 'comfyui.log').open('ab') as log:
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            subprocess.Popen([str(PORTABLE / 'python_embeded' / 'python.exe'), '-s', str(PORTABLE / 'ComfyUI' / 'main.py'), '--windows-standalone-build', '--listen', '127.0.0.1', '--port', str(parsed.port or 8188)], cwd=PORTABLE, stdout=log, stderr=log, creationflags=flags)
        for _ in range(120):
            await asyncio.sleep(1)
            try:
                await client.request('GET', '/system_stats')
                return web.json_response({'connected': True, 'message': 'ComfyUI 已启动。'})
            except ValueError:
                continue
        raise ValueError('ComfyUI 启动后未就绪，请查看 data/logs/comfyui.log。')


async def image(request):
    studio = request.app[STATE]
    job = studio.store.jobs.get(request.match_info['id'])
    if not job:
        raise web.HTTPNotFound()
    index = int(request.match_info['index'])
    if index < 0 or index >= len(job['images']):
        raise web.HTTPNotFound()
    if job.get('edit_purpose') == 'selection' and job.get('mask_ready') and index == 0:
        path = studio.edits.folder(job['edit_id']) / ('mask-' + job['id'] + '.png')
        return web.FileResponse(path)
    record = job['images'][index]
    if job.get('provider') == 'upload':
        path = studio.store.directory / 'generated' / 'uploads' / job['id'] / f'{index}.png'
        if not path.is_file(): raise web.HTTPNotFound(text='上传图片文件不存在。')
        return web.FileResponse(path)
    if job.get('provider') == 'novelai':
        path = studio.store.directory / 'generated' / 'novelai' / job['id'] / f'{index}.png'
        if not path.is_file():raise web.HTTPNotFound(text='NovelAI 图片文件不存在。')
        return web.FileResponse(path)
    kind = record.get('type', 'output')
    if kind not in {'output', 'temp', 'input'}:
        raise ValueError('无效的图片类型。')
    filename = record.get('filename', '')
    subfolder = record.get('subfolder', '').replace('\\', '/')
    if not filename or Path(filename).name != filename or '/' in filename or '\\' in filename:
        raise ValueError('无效的图片文件名。')
    root = (PORTABLE / 'ComfyUI' / kind).resolve()
    path = (root / subfolder / filename).resolve()
    if not path.is_relative_to(root):
        raise ValueError('无效的图片目录。')
    # Only the bundled default endpoint is associated with this output root.
    # Other localhost ports may belong to unrelated ComfyUI installations.
    image_origin = urlsplit(job['comfy_url'])
    is_bundled = image_origin.scheme == 'http' and image_origin.hostname in {'127.0.0.1', 'localhost'} and image_origin.port == 8188 and not image_origin.path.strip('/')
    if is_bundled and path.is_file():
        return web.FileResponse(path)
    params = {key: record.get(key, '') for key in ['filename', 'subfolder', 'type']}
    try:
        async with studio.session.get(job['comfy_url'] + '/view', params=params, timeout=aiohttp.ClientTimeout(total=15)) as response:
            if response.status == 404:
                raise web.HTTPNotFound(text='图片文件不存在，请检查 ComfyUI 输出目录。')
            if response.status != 200:
                raise web.HTTPServiceUnavailable(text='ComfyUI 暂时无法提供图片。')
            return web.Response(body=await response.read(), content_type=response.headers.get('Content-Type', 'image/png').split(';')[0])
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise web.HTTPServiceUnavailable(text='本地未找到图片，且 ComfyUI 暂时无法连接。') from None


async def index(request):
    return web.FileResponse(ROOT / 'static' / 'index.html')


def edit_job(studio, edit, graph, purpose, positive='', negative=''):
    workflow = new_workflow('半自动选区 · SAM' if purpose == 'selection' else '半自动局部修复 · WAI Illustrious SDXL', graph)
    workflow['random_seed'] = False
    now = datetime.now().astimezone().isoformat(timespec='seconds')
    job = {'id': str(uuid.uuid4()), 'client_id': str(uuid.uuid4()), 'workflow': workflow,
           'workflow_id': workflow['id'], 'values': {'seed': secrets.randbelow(2**48)},
           'comfy_url': studio.store.config['comfy_url'], 'created_at': now,
           'description': edit.get('region', '') if purpose == 'selection' else edit.get('description', positive),
           'positive_prompt': positive or 'region selection', 'negative_prompt': negative,
           'use_api': False, 'state': 'preparing', 'images': [], 'error': '',
           'edit_id': edit['id'], 'edit_purpose': purpose}
    if purpose == 'selection':
        job['selection_settings'] = {k: copy.deepcopy(edit.get(k)) for k in ('region', 'positive_coords', 'negative_coords')}
    edit[purpose + '_job_id'] = job['id']
    edit['latest_job_id'] = job['id']
    studio.edits.save(edit)
    studio.persist_job(job)
    studio.spawn(studio.run_job(job, copy.deepcopy(studio.store.config)))
    return job


async def edit_upload(request):
    studio = request.app[STATE]
    if request.content_type == 'application/json':
        data = await request.json()
        try: content = base64.b64decode(data['image_base64'], validate=True)
        except (ValueError, KeyError, TypeError): raise ValueError('图片编码不正确。') from None
    else:
        reader = await request.multipart()
        part = await reader.next()
        if part is None or part.name != 'image': raise ValueError('请选择一张原图。')
        content = bytearray()
        while chunk := await part.read_chunk():
            content.extend(chunk)
            if len(content) > MAX_BYTES: raise ValueError('图片须小于 20 MB。')
    return web.json_response(studio.edits.create(content))


async def edit_get(request):
    studio = request.app[STATE]
    edit = studio.edits.get(request.match_info['id'])
    return web.json_response(studio.edits.public(edit))


async def edit_original(request):
    return web.Response(body=request.app[STATE].edits.original(request.match_info['id']), content_type='image/png')


async def edit_select(request):
    studio = request.app[STATE]; data = await request.json()
    async with studio.edit_lock:
        edit = studio.edits.get(data['edit_id'])
        # Validate selection before uploading or submitting any work.
        graph = selection_graph('pending.png', data, (edit['width'], edit['height']))
        client = ComfyClient(studio.session, studio.store.config['comfy_url'])
        image = await client.upload_image(studio.edits.original(edit['id']), 'studio-edit-' + edit['id'] + '.png')
        graph['1']['inputs']['image'] = image
        edit.update(region=data.get('region', ''), positive_coords=json.loads(points(data, 'positive_coords', (edit['width'], edit['height']))), negative_coords=json.loads(points(data, 'negative_coords', (edit['width'], edit['height']))), threshold=graph['4']['inputs']['threshold'], comfy_image=image)
        return web.json_response(edit_job(studio, edit, graph, 'selection'))


async def edit_prompt(request):
    studio = request.app[STATE]; data = await request.json()
    edit = studio.edits.get(data['edit_id'])
    description = data.get('description', '').strip()
    if not description: raise ValueError('请填写修改要求。')
    template = studio.store.config['templates'].get('tags') or next(iter(studio.store.config['templates'].values()))
    instruction = f"根据原图，为 WAI Illustrious SDXL 的局部重绘生成最终画面标签。修改部位：{data.get('region', edit.get('region', ''))}。修改要求：{description}。必须保留：{data.get('preserve', '')}。只描述修改后的目标内容与必要的画风、人物特征，不要把‘不要改变’等操作指令写成生图标签。"
    matched = match_characters(studio.store.characters, description + '\n' + data.get('preserve', ''))
    result = await generate_prompt(studio.session, studio.store.config, instruction, character_template(template['text'], matched), image_url=prompt_image(studio.edits.original(edit['id'])))
    return web.json_response({**result, 'matched_characters': matched})


async def characters(request):
    store = request.app[STATE].store
    if request.method == 'GET':
        return web.json_response(list(store.characters.values()))
    if len(store.characters) >= 500:
        raise ValueError('人物库最多保存 500 位人物。')
    record = clean_record(await request.json(), store.characters)
    store.characters[record['id']] = record
    store.save('characters')
    return web.json_response(record)


async def character_item(request):
    store = request.app[STATE].store
    key = request.match_info['id']
    if key not in store.characters:
        raise web.HTTPNotFound()
    if request.method == 'DELETE':
        del store.characters[key]
        store.save('characters')
        return web.json_response({'ok': True})
    record = clean_record(await request.json(), store.characters, store.characters[key])
    store.characters[key] = record
    store.save('characters')
    return web.json_response(record)


async def character_match(request):
    data = await request.json()
    return web.json_response({'characters': match_characters(request.app[STATE].store.characters, data.get('description', ''))})


async def character_draft(request):
    studio = request.app[STATE]
    data = await request.json()
    name, description = data.get('name', ''), data.get('description', '')
    if not isinstance(name, str) or not name.strip() or len(name) > 64 or not isinstance(description, str) or not description.strip() or len(description) > 10000:
        raise ValueError('请填写人物名字和不超过 10000 字的人物描述。')
    instruction = '你是人物设定整理助手。根据描述提取可重复使用的固定外形：发色、发型、眼睛、特殊耳朵、角、尾巴、发夹、耳饰等。严格保留明确指定的特征，不臆造细节。只输出英文标签，完整词组。不要包含服装、动作、场景、构图、光线、临时表情、人物名字或多个备选版本。只返回 JSON，positive_prompt 为固定外形标签，negative_prompt 为空字符串。'
    result = await generate_prompt(studio.session, studio.store.config, description, instruction)
    return web.json_response({'name': name.strip(), 'appearance': result['positive_prompt'], 'clothing': data.get('clothing', ''), 'aliases': [], 'negative_prompt': '', 'notes': '', 'enabled': True})


async def edit_render(request):
    studio = request.app[STATE]; data = await request.json()
    async with studio.edit_lock:
        edit = studio.edits.get(data['edit_id'])
        if data.get('confirmed') is not True: raise ValueError('请先查看并确认选区。')
        mask_id = data.get('mask_job_id')
        job = studio.store.jobs.get(mask_id)
        if mask_id != edit.get('selection_job_id') or not job or job.get('edit_id') != edit['id'] or job.get('edit_purpose') != 'selection':
            raise ValueError('选区已更新，请重新预览并确认最新选区。')
        if job['state'] != 'done' or not job.get('images'): raise ValueError('选区尚未成功生成，请先完成定位。')
        positive = data.get('positive_prompt', '').strip()
        if not positive: raise ValueError('请生成或填写局部重绘提示词。')
        graph = redraw_graph('pending.png', 'pending-mask.png', data)
        seed = number(data, 'seed', secrets.randbelow(2**48), 0, 2**48-1, True)
        path = studio.edits.folder(edit['id']) / ('mask-' + mask_id + '.png')
        if not job.get('mask_ready') or not path.is_file(): raise ValueError('选区尚未验证，请重新定位。')
        mask = check_mask(path.read_bytes(), (edit['width'], edit['height']))
        client = ComfyClient(studio.session, studio.store.config['comfy_url'])
        # Re-upload both assets: render remains valid if ComfyUI input files were removed.
        graph['1']['inputs']['image'] = await client.upload_image(studio.edits.original(edit['id']), 'studio-edit-' + edit['id'] + '.png')
        graph['2']['inputs']['image'] = await client.upload_image(mask, 'studio-mask-' + mask_id + '.png')
        graph['9']['inputs']['seed'] = seed
        edit.update(description=data.get('description', positive), preserve=data.get('preserve', ''), confirmed_mask_job_id=mask_id)
        result = edit_job(studio, edit, graph, 'render', positive, data.get('negative_prompt', '').strip())
        result['values']['seed'] = seed
        studio.persist_job(result)
        return web.json_response(result)


async def lifecycle(app):
    studio = app[STATE]
    async with aiohttp.ClientSession() as session:
        studio.session = SystemProxySession(session)
        for job in studio.store.jobs.values():
            if job['state'] not in FINAL_STATES:
                if job.get('provider') == 'novelai':
                    job.update(state='unknown' if job['state']=='submitting' else 'interrupted',error='服务已重启，NovelAI 请求未自动重发；请先检查服务商记录和额度。')
                    studio.persist_job(job)
                elif job.get('prompt_id'):
                    studio.spawn(studio.monitor(job))
                else:
                    job.update(state='unknown' if job['state'] == 'submitting' else 'interrupted', error='软件已重启，前一次请求未确认完成，请检查 ComfyUI 队列后再操作。')
                    studio.persist_job(job)
        yield
        pending = list(studio.tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


def create_app(data_dir=None):
    app = web.Application(middlewares=[guard], client_max_size=30 * 1024 * 1024)
    app[STATE] = Studio(data_dir or ROOT / 'data')
    app.cleanup_ctx.append(lifecycle)
    nai_routes.register(app, STATE)
    nai_redraw.register(app, STATE)
    prompt_presets.register(app, STATE)
    comic_review.register(app, STATE)
    comic_editor.register(app, STATE)
    nai_chat.register(app, STATE)
    history_tags.register(app, STATE)
    app.router.add_get('/', index)
    app.router.add_get('/api/bootstrap', bootstrap)
    app.router.add_get('/api/config', config)
    app.router.add_put('/api/config', config)
    app.router.add_post('/api/models', models)
    app.router.add_post('/api/novelai/test', novelai_test)
    app.router.add_get('/api/workflow-sources', sources)
    app.router.add_get('/api/workflows', workflows)
    app.router.add_post('/api/workflows', workflows)
    app.router.add_put('/api/workflows/{id}', workflow_item)
    app.router.add_delete('/api/workflows/{id}', workflow_item)
    app.router.add_post('/api/prompt', prompt)
    app.router.add_get('/api/characters', characters)
    app.router.add_post('/api/characters', characters)
    app.router.add_post('/api/characters/match', character_match)
    app.router.add_post('/api/characters/draft', character_draft)
    app.router.add_put('/api/characters/{id}', character_item)
    app.router.add_delete('/api/characters/{id}', character_item)
    app.router.add_get('/api/jobs', jobs)
    app.router.add_post('/api/jobs', jobs)
    app.router.add_get('/api/jobs/{id}', job_item)
    app.router.add_post('/api/jobs/{id}/resume', job_item)
    app.router.add_delete('/api/jobs/{id}', job_item)
    app.router.add_get('/api/comfy/{action}', comfy)
    app.router.add_post('/api/comfy/{action}', comfy)
    app.router.add_get('/api/images/{id}/{index}', image)
    app.router.add_post('/api/edit/upload', edit_upload)
    app.router.add_post('/api/edit/select', edit_select)
    app.router.add_post('/api/edit/prompt', edit_prompt)
    app.router.add_post('/api/edit/render', edit_render)
    app.router.add_get('/api/edit/{id}/original', edit_original)
    app.router.add_get('/api/edit/{id}', edit_get)
    if (ROOT / 'static').exists():
        app.router.add_static('/static/', ROOT / 'static')
    return app


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8190)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    web.run_app(create_app(), host='127.0.0.1', port=args.port, access_log=None)
