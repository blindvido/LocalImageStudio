"""Small image-to-image entry: source snapshots and immutable child jobs."""
import copy
import hashlib
import io
import json
import math
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from PIL import Image, ImageOps
import novelai
from nai_panels import normalize_nai_prompt


async def source_bytes(studio, job, index):
    record = job['images'][index]
    if job.get('provider') == 'novelai':
        path = studio.store.directory / 'generated' / 'novelai' / job['id'] / f'{index}.png'
    else:
        kind = record.get('type', 'output')
        if kind not in {'output', 'input', 'temp'}:
            raise ValueError('无效的图片类型。')
        root = (Path(__file__).resolve().parent.parent / 'ComfyUI' / kind).resolve()
        path = (root / record.get('subfolder', '') / record['filename']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('无效的图片路径。')
        origin = urlsplit(job.get('comfy_url', ''))
        bundled = origin.hostname in {'localhost', '127.0.0.1'} and origin.port == 8188 and not origin.path.strip('/')
        if not bundled or not path.is_file():
            async with studio.session.get(job['comfy_url'] + '/view', params=record,
                                          timeout=aiohttp.ClientTimeout(total=20), allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError('无法读取所选底图，请确认 ComfyUI 在线。')
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > 30 * 1024 * 1024:
                        raise ValueError('底图超过 30 MB。')
                return bytes(data)
    if not path.is_file():
        raise ValueError('所选底图文件不存在。')
    if path.stat().st_size > 30 * 1024 * 1024:
        raise ValueError('底图超过 30 MB。')
    return path.read_bytes()


def register(app, state_key):
    async def redraw(request):
        studio = request.app[state_key]
        data = await request.json()
        if set(data) - {'source_job_id', 'source_index', 'strength', 'request_id'}:
            raise ValueError('重绘请求含不支持的字段。')
        request_id = data.get('request_id')
        if not isinstance(request_id, str) or not request_id or len(request_id) > 100:
            raise ValueError('请提供有效的请求 ID。')
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()
        # Serialize preparation too: double clicks cannot create two children.
        async with studio.edit_lock:
            for old in studio.store.jobs.values():
                if old.get('request_id') == request_id:
                    if old.get('request_fingerprint') != fingerprint:
                        raise ValueError('请求 ID 已用于其他内容。')
                    return web.json_response(old)
            source = studio.store.jobs.get(data.get('source_job_id'))
            index = data.get('source_index', 0)
            if not source or source['state'] != 'done' or type(index) is not int or not 0 <= index < len(source['images']):
                raise ValueError('请选择已完成任务中的有效图片。')
            strength = data.get('strength')
            if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1:
                raise ValueError('Strength 须为 0–1 之间的数字。')
            if not studio.store.config.get('novelai_token'):
                raise ValueError('请先在设置填写 NovelAI Token。')
            raw = await source_bytes(studio, source, index)
            try:
                with Image.open(io.BytesIO(raw)) as opened:
                    if opened.width * opened.height > 16000000:
                        raise ValueError('底图尺寸过大。')
                    img = ImageOps.exif_transpose(opened).convert('RGB')
                    scale = min(1, 2048 / img.width, 2048 / img.height, math.sqrt(2800000 / (img.width * img.height)))
                    size = tuple(max(64, round(n * scale / 64) * 64) for n in img.size)
                    img = img.resize(size, Image.Resampling.LANCZOS)
                    output = io.BytesIO(); img.save(output, 'PNG'); content = output.getvalue()
            except (OSError, Image.DecompressionBombError):
                raise ValueError('底图文件无法解码。') from None
            values = dict(novelai.DEFAULTS)
            values['MODEL_NAME'] = studio.store.config['novelai_model']
            if source.get('provider') == 'novelai':
                values.update({k: v for k, v in source['values'].items() if k in novelai.DEFAULTS})
            values.update(width=size[0], height=size[1])
            prompt = normalize_nai_prompt(source.get('nai_prompt') or {
                'base_positive': source['positive_prompt'], 'base_negative': source.get('negative_prompt', ''), 'characters': []})
            body = novelai.payload(prompt['base_positive'], prompt['base_negative'], values,
                                   characters=prompt['characters'], img2img={'image': content, 'strength': strength})
            # Store the image once; JSON retains a path and hash, never a huge base64 blob.
            del body['parameters']['image']
            job_id = str(uuid.uuid4())
            folder = studio.store.directory / 'generated' / 'novelai' / job_id
            folder.mkdir(parents=True, exist_ok=False)
            (folder / 'source.png').write_bytes(content)
            job = {'id': job_id, 'client_id': str(uuid.uuid4()), 'provider': 'novelai',
                   'workflow_id': 'novelai', 'workflow': {'id': 'novelai', 'name': 'NovelAI 重绘 · ' + values['MODEL_NAME'], 'template': 'tags'},
                   'request_id': request_id, 'request_fingerprint': fingerprint, 'parent_job_id': source['id'],
                   'img2img': {'source_job_id': source['id'], 'source_index': index, 'strength': strength, 'noise': 0,
                               'source_file': 'source.png', 'sha256': hashlib.sha256(content).hexdigest(),
                               'original_sha256': hashlib.sha256(raw).hexdigest()},
                   'created_at': datetime.now().astimezone().isoformat(), 'values': values, 'nai_prompt': prompt,
                   'description': source.get('description', ''), 'positive_prompt': prompt['base_positive'],
                   'negative_prompt': prompt['base_negative'], 'use_api': False, 'state': 'preparing',
                   'images': [], 'error': '', 'matched_characters': [], 'request_snapshot': body}
            if 'work_tags' in source:
                job['work_tags'] = copy.deepcopy(source['work_tags'])
            studio.persist_job(job)
            studio.spawn(studio.run_job(job, copy.deepcopy(studio.store.config)))
            return web.json_response(job)
    app.router.add_post('/api/nai/redraw', redraw)
