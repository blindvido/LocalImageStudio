"""Human image decisions; immutable generation records remain separate."""
import copy
import hashlib
import json
import re
import secrets
import uuid
import io
import base64
import binascii
from PIL import Image, ImageOps
from history_tags import effective_tags
from nai_panels import normalize_nai_prompt
from nai_redraw import source_bytes
from datetime import datetime
from pathlib import Path
from aiohttp import web
import novelai
from comic_panels import clean_panel


def now():
    return datetime.now().astimezone().isoformat()


def check_revision(store, revision):
    if type(revision) is not int or revision != store.comic_review['revision']:
        raise ValueError('审核记录已更新，请刷新后重试。')


def image_job(store, job_id, index):
    job = store.jobs.get(job_id)
    if not job or job.get('state') != 'done' or type(index) is not int or not 0 <= index < len(job.get('images', [])):
        raise ValueError('请选择已完成任务中的有效图片。')
    return job


def asset_key(job_id, index):
    return f'{job_id}:{index}'


def panel_for(store, job, index):
    return store.comic_review['assets'].get(asset_key(job['id'], index), {}).get('panel_id') or job.get('panel_id', '')


def commit(store, event):
    store.comic_review['revision'] += 1
    store.comic_review['events'].append({**event, 'at': now(), 'revision': store.comic_review['revision']})
    store.save('comic_review')


def bind_image(store, job_id, index, panel_id, revision):
    check_revision(store, revision)
    image_job(store, job_id, index)
    if panel_id not in store.panels:
        raise ValueError('目标分镜不存在。')
    key = asset_key(job_id, index)
    old = store.comic_review['assets'].get(key, {})
    if old.get('status') == 'approved' and old.get('panel_id') != panel_id:
        raise ValueError('请先撤销通过，再修改图片归属。')
    store.comic_review['assets'][key] = {**old, 'panel_id': panel_id, 'status': old.get('status', 'pending')}
    commit(store, {'action': 'bind', 'job_id': job_id, 'image_index': index, 'panel_id': panel_id})


def set_review(store, job_id, index, status, note, revision):
    check_revision(store, revision)
    job = image_job(store, job_id, index)
    if status not in {'approved', 'pending', 'changes_requested'} or not isinstance(note, str) or len(note) > 4000:
        raise ValueError('审核状态或意见无效。')
    panel_id = panel_for(store, job, index)
    if panel_id not in store.panels:
        raise ValueError('先将图片归档到一个分镜。')
    # Only one selected image per panel. Prior decisions remain in the event ledger.
    if status == 'approved':
        for key, a in store.comic_review['assets'].items():
            if a.get('panel_id') == panel_id and a.get('status') == 'approved':
                a['status'] = 'pending'
    store.comic_review['assets'][asset_key(job_id, index)] = {
        'panel_id': panel_id, 'status': status, 'note': note, 'reviewed_at': now(),
        'job_id': job_id, 'image_index': index}
    commit(store, {'action': status, 'job_id': job_id, 'image_index': index, 'panel_id': panel_id, 'note': note})


def import_script(store, data):
    if not isinstance(data, dict): raise ValueError('台本须为JSON对象。')
    project = str(data.get('project') or data.get('project_name') or '坠落')
    version = str(data.get('version') or data.get('script_version') or '')
    chapter = str(data.get('chapter') or data.get('chapter_id') or '')
    if not chapter or not version: raise ValueError('台本须包含chapter和version（或script_version）。')
    entries = []
    for page in data.get('pages', data.get('panels', [])):
        if not isinstance(page, dict): raise ValueError('每页须为对象。')
        for p in page.get('panels', [page]):
            entries.append({**page, **p})
    if len(entries) > 5000 or not data.get('pages',data.get('panels')): raise ValueError('台本须包含页面，最多5000个分镜。')
    updates = {}
    for e in entries:
        page = str(e.get('page_id') or e.get('page') or '')
        panel = str(e.get('panel_id') or e.get('panel') or 'K01')
        if not page: raise ValueError('分镜缺少page_id。')
        identity = json.dumps([project, chapter, version, page, panel], ensure_ascii=False)
        key = 'script-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
        existing = next((p for p in store.panels.values() if all(p.get(k,'')==v for k,v in [('project',project),('chapter',chapter),('script_version',version),('page',page),('panel',panel)])), None)
        if existing: key = existing['id']
        if key in updates: raise ValueError('台本中存在重复页格编号。')
        if key in store.panels:
            updates[key] = store.panels[key]
            continue  # Re-import never overwrites edited or approved work.
        prompt = e.get('nai_prompt') or {'base_positive': e.get('final_prompt', e.get('prompt', '')), 'base_negative': e.get('negative_prompt', ''), 'characters': []}
        if not e.get('nai_prompt'):
            for c in e.get('characters', []):
                cid = c.get('source_id') or c.get('character_id')
                if cid in store.characters:
                    prompt['characters'].append({'character_id': cid, 'positive': c.get('positive', c.get('prompt', '')), 'direction': c.get('additional_prompt', c.get('direction', '')), 'negative': c.get('negative_prompt', ''), 'center': {'x': .5, 'y': .5}})
        record = clean_panel({'project': project, 'chapter': chapter, 'script_version': version, 'page': page, 'panel': panel,
                              **{k:e[k] for k in ('title','camera_notes','lettering_notes','modules','private_notes') if k in e},
                              'visible_description': e.get('visual', e.get('visual_brief', e.get('source_visual_brief', ''))),
                              'dialogue': e.get('dialogue', e.get('lettering_only', {}).get('dialogue', '')),
                              'nai_prompt': prompt, 'values': e.get('values') or {'MODEL_NAME': data.get('model_intent') or store.config['novelai_model']}}, {**{c['id']:c for c in e.get('character_snapshots',[])},**store.characters})
        record['id'] = key
        updates[key] = record
    store.panels.update(updates)
    catalog=store.comic_review.setdefault('page_catalog',[])
    for page in data.get('pages',[]):
        name=page.get('page_id') or page.get('page')
        record={'project':project,'chapter':chapter,'script_version':version,'page':name}
        if name and record not in catalog: catalog.append(record)
    store.save('panels')
    commit(store, {'action': 'import_script', 'project': project, 'chapter': chapter, 'script_version': version, 'count': len(updates)})
    return list(updates)


def order(panel):
    def natural(value): return tuple((0, int(v)) if v.isdigit() else (1, v) for v in re.split(r'(\d+)', str(value)))
    return tuple(natural(panel.get(k, '')) for k in ('project', 'chapter', 'script_version', 'page', 'panel'))


def board(store):
    panels = [{**copy.deepcopy(p), 'candidates': [], 'active_jobs': []} for p in sorted(store.panels.values(), key=order)]
    lookup = {p['id']: p for p in panels}
    unassigned = []
    for job in sorted(store.jobs.values(), key=lambda j: j.get('created_at', ''), reverse=True):
        if job.get('state') != 'done':
            if job.get('panel_id') in lookup:
                lookup[job['panel_id']]['active_jobs'].append({k: job.get(k, '') for k in ('id','state','error')})
            continue
        for index, image in enumerate(job.get('images', [])):
            a = store.comic_review['assets'].get(asset_key(job['id'], index), {})
            pid = panel_for(store, job, index)
            asset = {'job_id': job['id'], 'image_index': index, 'panel_id': pid,
                     'url': f"/api/images/{job['id']}/{index}", 'filename': image.get('filename', ''),
                     'status': a.get('status', 'pending'), 'note': a.get('note', ''),
                     'created_at': job.get('created_at', ''), 'provider': job.get('provider', 'comfyui'),
                     'work_tags': effective_tags(job), 'search_prompt': job.get('positive_prompt', '') + ' ' + json.dumps(job.get('nai_prompt', {}), ensure_ascii=False),
                     'values': job.get('values', {}), 'description': job.get('description', ''),
                     'regenerable': job.get('provider') == 'novelai'}
            (lookup[pid]['candidates'] if pid in lookup else unassigned).append(asset)
    return {'revision': store.comic_review['revision'], 'panels': panels, 'unassigned': unassigned, 'page_catalog': copy.deepcopy(store.comic_review.get('page_catalog', []))}


def selection_export(store):
    result = {'schema_version': 1, 'revision': store.comic_review['revision'], 'selected': [], 'missing': []}
    for p in board(store)['panels']:
        location = {k: p.get(k, '') for k in ('id', 'project', 'chapter', 'script_version', 'page', 'panel', 'dialogue', 'visible_description')}
        a = next((a for a in p['candidates'] if a['status'] == 'approved'), None)
        if a:
            job = store.jobs[a['job_id']]
            local = store.directory / 'generated' / ('uploads' if job.get('provider') == 'upload' else 'novelai') / job['id'] / f"{a['image_index']}.png"
            result['selected'].append({**location, **a, 'image_path': str(local.resolve()) if job.get('provider') in ('novelai', 'upload') and local.is_file() else None,
                                       'image_url': a['url'], 'nai_prompt': job.get('nai_prompt'), 'positive_prompt': job.get('positive_prompt', ''), 'negative_prompt': job.get('negative_prompt', '')})
        else: result['missing'].append(location)
    return result


def register(app, state_key):
    async def upload(request):
        studio = request.app[state_key]
        data = await request.json()
        rid, pid = data.get('request_id'), data.get('panel_id')
        if not isinstance(rid, str) or not 1 <= len(rid) <= 100: raise ValueError('缺少请求ID。')
        try: raw = base64.b64decode(data.get('image', ''), validate=True)
        except (ValueError, TypeError, binascii.Error): raise ValueError('无效的图片数据。') from None
        if not raw or len(raw) > 15 * 1024 * 1024: raise ValueError('图片须小于15MB。')
        signature = hashlib.sha256(raw + str(pid).encode()).hexdigest()
        async with studio.edit_lock:
            if pid not in studio.store.panels: raise ValueError('目标分镜不存在。')
            for old in studio.store.jobs.values():
                if old.get('request_id') == rid:
                    if old.get('request_fingerprint') != signature: raise ValueError('请求ID已被其他内容使用。')
                    return web.json_response(board(studio.store))
            try:
                with Image.open(io.BytesIO(raw)) as im:
                    if im.format not in ('PNG','JPEG','WEBP') or im.width * im.height > 40000000:
                        raise ValueError('请上传不超过4000万像素的 PNG、JPEG 或 WebP。')
                    extension = {'PNG':'png','JPEG':'jpg','WEBP':'webp'}[im.format]
                    im = ImageOps.exif_transpose(im).convert('RGBA')
                    buf = io.BytesIO(); im.save(buf, 'PNG'); content = buf.getvalue()
                    size = im.size
            except (OSError, Image.DecompressionBombError): raise ValueError('无法读取图片。') from None
            jid = str(uuid.uuid4())
            folder = studio.store.directory / 'generated' / 'uploads' / jid
            folder.mkdir(parents=True, exist_ok=False)
            (folder / '0.png').write_bytes(content)
            (folder / ('original.' + extension)).write_bytes(raw)
            name = str(data.get('filename', '上传图片'))[:255]
            job = {'id':jid,'provider':'upload','panel_id':pid,'state':'done','created_at':now(),
                   'description':name,'images':[{'filename':'0.png','type':'output'}],
                   'values':{'width':size[0],'height':size[1]},'positive_prompt':'','negative_prompt':'',
                   'request_id':rid,'request_fingerprint':signature,'upload_filename':name,
                   'original_sha256':hashlib.sha256(raw).hexdigest(),'error':'','use_api':False}
            studio.persist_job(job)
            commit(studio.store, {'action':'upload','job_id':jid,'panel_id':pid})
            return web.json_response(board(studio.store))

    async def get_board(request): return web.json_response(board(request.app[state_key].store))
    async def exported(request): return web.json_response(selection_export(request.app[state_key].store))
    async def mutate(request):
        studio = request.app[state_key]
        data = await request.json()
        async with studio.edit_lock:
            action = request.match_info['action']
            if action == 'import': import_script(studio.store, data)
            elif action == 'bind': bind_image(studio.store, data.get('job_id'), data.get('image_index'), data.get('panel_id'), data.get('revision'))
            elif action == 'decision': set_review(studio.store, data.get('job_id'), data.get('image_index'), data.get('status'), data.get('note', ''), data.get('revision'))
            else: raise web.HTTPNotFound()
        return web.json_response(board(studio.store))

    async def regenerate(request):
        studio = request.app[state_key]
        data = await request.json()
        rid = data.get('request_id')
        if not isinstance(rid, str) or not rid or len(rid) > 100: raise ValueError('缺少请求ID。')
        signature = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        async with studio.edit_lock:
            for job in studio.store.jobs.values():
                if job.get('request_id') == rid:
                    if job.get('request_fingerprint') != signature: raise ValueError('请求ID已被其他内容使用。')
                    return web.json_response(job)
            first_panel=studio.store.panels.get(data.get('panel_id')) if not data.get('job_id') else None
            if first_panel:
                if data.get('panel_revision') != first_panel['revision']: raise ValueError('分镜已更新，请刷新后生成。')
                old={'id':'','provider':'novelai','values':first_panel['values'],'nai_prompt':first_panel['nai_prompt'],
                     'positive_prompt':first_panel['nai_prompt']['base_positive'],'negative_prompt':first_panel['nai_prompt']['base_negative'],
                     'workflow':{'id':'novelai','name':'NovelAI','template':'tags'},'workflow_id':'novelai',
                     'description':first_panel['visible_description'],'comfy_url':''}
            else:
                old = image_job(studio.store, data.get('job_id'), data.get('image_index'))
            if old.get('provider') != 'novelai':
                raise ValueError('此入口仅支持 NovelAI 图片微调。')
            if not studio.store.config.get('novelai_token'): raise ValueError('请先配置NovelAI。')
            pid = first_panel['id'] if first_panel else panel_for(studio.store, old, data['image_index'])
            if pid not in studio.store.panels: raise ValueError('请先归档到分镜。')
            overrides = data.get('values', {})
            if not isinstance(overrides, dict) or set(overrides) - set(novelai.DEFAULTS):
                raise ValueError('生成参数含不支持的字段。')
            values = {**copy.deepcopy(old['values']), **overrides}
            if data.get('random_seed', True): values['seed'] = secrets.randbelow(2**32)
            prompt = normalize_nai_prompt(data.get('nai_prompt', old.get('nai_prompt') or {'base_positive': old['positive_prompt'], 'base_negative': old.get('negative_prompt', ''), 'characters': []}))
            mode = data.get('mode', 'txt2img')
            if mode not in ('txt2img', 'img2img'): raise ValueError('无效的生成模式。')
            if first_panel and mode == 'img2img': raise ValueError('请先选择一张图片作为重绘底图。')
            payload = novelai.payload(prompt['base_positive'], prompt['base_negative'], values, characters=prompt['characters'])
            content = None
            if mode == 'img2img':
                raw = await source_bytes(studio, old, data['image_index'])
                with Image.open(io.BytesIO(raw)) as im:
                    im = ImageOps.exif_transpose(im).convert('RGB').resize((int(values['width']), int(values['height'])), Image.Resampling.LANCZOS)
                    buf = io.BytesIO(); im.save(buf, 'PNG'); content = buf.getvalue()
                payload = novelai.payload(prompt['base_positive'], prompt['base_negative'], values, characters=prompt['characters'], img2img={'image':content,'strength':data.get('strength', .35)})
                del payload['parameters']['image']
            job = {k: copy.deepcopy(old[k]) for k in ('provider','workflow','workflow_id','schema_version','description','comfy_url','matched_characters','positive_prompt','negative_prompt','panel_revision','panel_snapshot') if k in old}
            job.update(id=str(uuid.uuid4()), client_id=str(uuid.uuid4()), parent_job_id=old['id'], panel_id=pid,
                       request_id=rid, request_fingerprint=signature, state='preparing', images=[], error='',
                       created_at=now(), values=values, request_snapshot=payload, use_api=False, nai_prompt=prompt)
            job['panel_snapshot'] = {k: copy.deepcopy(studio.store.panels[pid].get(k)) for k in ('id','revision','project','chapter','page','panel','script_version','dialogue','visible_description','character_snapshots')}
            job['workflow'] = {**job['workflow'], 'values': values, 'fixed_positive': '', 'fixed_negative': ''}
            job['positive_prompt'] = prompt['base_positive']
            job['negative_prompt'] = prompt['base_negative']
            if content is not None:
                folder = studio.store.directory / 'generated' / 'novelai' / job['id']
                folder.mkdir(parents=True, exist_ok=False)
                (folder / 'source.png').write_bytes(content)
                job['img2img'] = {'source_job_id':old['id'], 'source_index':data['image_index'], 'strength':data.get('strength', .35), 'noise':0, 'source_file':'source.png', 'sha256':hashlib.sha256(content).hexdigest(), 'original_sha256':hashlib.sha256(raw).hexdigest()}
            if 'work_tags' in old:
                job['work_tags'] = copy.deepcopy(old['work_tags'])
            studio.persist_job(job)
            studio.spawn(studio.run_job(job, copy.deepcopy(studio.store.config)))
            return web.json_response(job)

    app.router.add_get('/api/comic-review', get_board)
    app.router.add_get('/api/comic-review/selection', exported)
    app.router.add_post('/api/comic-review/regenerate', regenerate)
    app.router.add_post('/api/comic-review/generate', regenerate)
    app.router.add_post('/api/comic-review/upload', upload)
    app.router.add_post('/api/comic-review/{action}', mutate)
