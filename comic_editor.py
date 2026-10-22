"""Chapter editing with stable panel identities and portable Skill input exports."""
import copy
import hashlib
import io
import json
import re
import uuid
import zipfile
from pathlib import Path
from aiohttp import web
from comic_panels import clean_panel
from comic_review import board, check_revision, commit, panel_for, selection_export, order, now

SCOPE = ('project', 'chapter', 'script_version')
MODULES = ('style', 'scene', 'camera', 'action')


def scope(data):
    result = {}
    for k in SCOPE:
        value = data.get(k)
        if not isinstance(value, str) or not value.strip() or len(value) > 100:
            raise ValueError('请指定项目、话和台本版本。')
        result[k] = value
    return result


def matches(record, where):
    return all(record.get(k, '') == v for k, v in where.items())


def chapter_pages(store, where):
    records = sorted((p for p in store.panels.values() if matches(p, where)), key=order)
    names = {p['page'] for p in records}
    names.update(p['page'] for p in store.comic_review.get('page_catalog', []) if matches(p, where))
    def natural(v):
        import re
        return [(0,int(x)) if x.isdigit() else (1,x) for x in re.split(r'(\d+)',v)]
    return [{'page':name, 'panels':[copy.deepcopy(p) for p in records if p['page']==name]} for name in sorted(names,key=natural)]


def save_chapter(store, data):
    check_revision(store, data.get('revision'))
    where = scope(data)
    pages = data.get('pages')
    if not isinstance(pages, list) or len(pages)>500: raise ValueError('每话最多500页。')
    old = {p['id']:p for p in store.panels.values() if matches(p, where)}
    updates, catalog, seen = {}, [], set()
    for i, page in enumerate(pages, 1):
        if not isinstance(page, dict) or not isinstance(page.get('panels'),list): raise ValueError('页面格式无效。')
        name = f'P{i:02d}'
        catalog.append({**where,'page':name})
        if len(page['panels'])>100: raise ValueError('每页最多100格。')
        for j, entry in enumerate(page['panels'],1):
            if not isinstance(entry,dict): raise ValueError('分镜格式无效。')
            pid = entry.get('id')
            if pid and (pid not in old or pid in seen): raise ValueError('分镜已变更或重复，请刷新台本。')
            if pid and entry.get('revision') != old[pid]['revision']:
                raise ValueError('分镜已被其他窗口修改，请刷新台本。')
            if pid: seen.add(pid)
            # Only storyboard fields are editable here. Prompt/seed changes use the generation editor.
            fields = {k:entry[k] for k in ('title','visible_description','camera_notes','lettering_notes','private_notes','dialogue') if k in entry}
            if isinstance(fields.get('dialogue'),list):
                for line in fields['dialogue']:
                    if not isinstance(line,dict) or set(line)-{'speaker','text','type'} or not isinstance(line.get('text'),str) or not isinstance(line.get('speaker',''),str) or line.get('type','dialogue') not in ('dialogue','caption','sfx'):
                        raise ValueError('台词须包含说话者、文本及对白／旁白／音效类型。')
            record = clean_panel({**fields, **where,'page':name,'panel':f'K{j:02d}', **({} if pid else {'nai_prompt':{'base_positive':'','base_negative':'','characters':[]},'values':{'MODEL_NAME':store.config['novelai_model']}})}, store.characters, old.get(pid))
            updates[record['id']] = record
    if len(updates)>5000: raise ValueError('每话最多5000格。')
    removed = set(old)-set(updates)
    # Preserve old chapter snapshots before committing any mutation.
    history = {'action':'edit_script','scope':where,'before':chapter_pages(store,where),'removed_panels':sorted(removed)}
    for job in store.jobs.values():
        for index in range(len(job.get('images',[]))):
            if panel_for(store,job,index) in removed:
                key=f"{job['id']}:{index}"
                asset=store.comic_review['assets'].get(key,{})
                store.comic_review['assets'][key]={**asset,'panel_id':'','status':'pending','note':asset.get('note','')}
        if job.get('panel_id') in removed:
            job['panel_id']=''  # Actual historical panel_snapshot remains immutable.
    store.panels = {**{k:v for k,v in store.panels.items() if k not in old},**updates}
    store.comic_review['page_catalog']=[p for p in store.comic_review.get('page_catalog',[]) if not matches(p,where)]+catalog
    store.save('panels');store.save('jobs')
    commit(store, history)
    return board(store)


def export_bundle(store, where):
    pages=chapter_pages(store,where)
    script={'schema_version':1,'project':where['project'],'chapter':where['chapter'],'version':where['script_version'],'revision':store.comic_review['revision'], 'pages':[]}
    markdown=[f"# {where['project']} · {where['chapter']} · {where['script_version']}",'']
    for page in pages:
        script['pages'].append({'page_id':page['page'],'panels':[{**p,'page_id':p['page'],'panel_id':p['panel'],'visual':p['visible_description']} for p in page['panels']]})
        markdown += [f"## {page['page']}",'']
        for p in page['panels']:
            markdown += [f"### {p['panel']} {p.get('title','')}",p['visible_description'],f"镜头：{p.get('camera_notes','')}",f"留白／排字：{p.get('lettering_notes','')}",'']
            lines=p.get('dialogue',[])
            if isinstance(lines,str): markdown.append(lines)
            else: markdown.extend(f"- [{x.get('type','dialogue')}] {x.get('speaker','')}：{x.get('text','')}" for x in lines)
    script_text='\n'.join(markdown)+'\n'
    files={'script/storyboard.json':json.dumps(script,ensure_ascii=False,indent=2),'script/storyboard.md':script_text}
    source_paths=['script/storyboard.md']
    # Only known project memory is eligible; never walk unrelated directories or include config.
    memory_candidates=[Path('E:/坠落漫画制作中心/memory.md'),Path('E:/ComfyUI_windows_portable/output/坠落漫画项目/memory.md')] if where['project']=='坠落' else []
    memory=next((p for p in memory_candidates if p.is_file()),None)
    if memory:
        files['memory.md']=memory.read_text(encoding='utf-8-sig');source_paths.append('memory.md')
    selection=selection_export(store)
    selection={**selection,'selected':[p for p in selection['selected'] if matches(p,where)],'missing':[p for p in selection['missing'] if matches(p,where)]}
    files['selection.json']=json.dumps(selection,ensure_ascii=False,indent=2)
    manifest={'schema_version':1,'created_at':now(),'scope':where,'revision':script['revision'],'memory_included':bool(memory),'panels':[],'sources':[]}
    for page_index, page in enumerate(pages,1):
        for panel_index, p in enumerate(page['panels'],1):
            prompt=p.get('nai_prompt',{})
            modules=copy.deepcopy(p.get('modules'))
            legacy=modules is None
            if legacy:
                modules={k:{'positive':'','negative':''} for k in MODULES}
                modules['camera']={'positive':prompt.get('base_positive',''),'negative':prompt.get('base_negative','')}
            snapshots={c['id']:c for c in p.get('character_snapshots',[])}
            chars=[{'id':c['character_id'] if re.fullmatch(r'[A-Za-z0-9_-]{1,64}',c['character_id']) else f'character_{n+1}', 'name':snapshots.get(c['character_id'],{}).get('name',''), 'positive':c['positive'],'direction':c.get('direction',''),'negative':c['negative'],'center':c['center']} for n,c in enumerate(prompt.get('characters',[]))]
            page_id,panel_id=f'P{page_index:02d}',f'K{panel_index:02d}'
            spec={'project':p['project'],'page_id':page_id,'panel_id':panel_id,'title':p.get('title',''),'script_summary':p['visible_description'],'dialogue':p.get('dialogue',[]),'sources':source_paths,'generation_mode':'storyboard_panel','producer':'绘境台本编辑站（人工编辑；生成模型未指定）','modules':modules,'characters':chars,'params':p['values']}
            path=f"inputs/{page_id}/{panel_id}.json"
            files[path]=json.dumps(spec,ensure_ascii=False,indent=2)
            manifest['panels'].append({'id':p['id'],'page_id':page_id,'panel_id':panel_id,'source_page':p['page'],'source_panel':p['panel'],'character_ids':{c['id']:original['character_id'] for c,original in zip(chars,prompt.get('characters',[]))},'input':path,'legacy_flat_prompt':legacy,'ready_for_prepare':bool(p['visible_description'].strip() and any(x['positive'].strip() for x in modules.values())),'camera_notes':p.get('camera_notes',''),'lettering_notes':p.get('lettering_notes','')})
    for name in source_paths:manifest['sources'].append({'path':name,'sha256':hashlib.sha256(files[name].encode()).hexdigest()})
    files['manifest.json']=json.dumps(manifest,ensure_ascii=False,indent=2)
    files['README.md']='''# 绘境 · Skill 分镜交接包

解压到独立目录。script/storyboard.json 可重新导入绘境；Markdown用于阅读。
inputs/Pxx/Kxx.json 严格使用 comic-storyboard-image-pipeline 的 prepare 输入契约。
按顺序调用该 Skill scripts/panel_bundle.py prepare --root <解压目录> --input <input文件>，再用 nai-preview 检查。导出本身不生成 prompt.vN 或冒充已准备/已出图。
manifest.ready_for_prepare=false 表示缺画面说明或提示词，须先补齐。memory_included=false 时请在生成前读取实际项目记忆，不把空文件当已确认设定。
历史主提示词未记录模块来源时，完整原文放入camera模块，其余留空；legacy_flat_prompt=true。这仅保持拼接原文，不声称已识别镜头词。不会猜测预设名称或来源。
selection.json 只将用户实际通过的具体图片列入selected；缺图列入missing。原图仍在工作站，导出不复制或修改图片。实际生成Prompt/Seed以selection和任务实际记录为准，inputs是台本计划参数。
台词、镜头要求、留白保存在台本和manifest，默认不把中文对白添加到生图Prompt。后续Pedit须读取这些字段并人工核对阅读顺序。
'''
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for name,content in files.items():z.writestr(name,content.encode('utf-8'))
    return output.getvalue()


def register(app,state_key):
    async def save(request):
        studio=request.app[state_key]
        data=await request.json()
        async with studio.edit_lock:return web.json_response(save_chapter(studio.store,data))
    async def exported(request):
        studio=request.app[state_key]
        async with studio.edit_lock:content=export_bundle(studio.store,scope(request.query))
        return web.Response(body=content,content_type='application/zip',headers={'Content-Disposition':'attachment; filename="comic-storyboard.zip"'})
    app.router.add_post('/api/comic-editor/save',save)
    app.router.add_get('/api/comic-editor/export',exported)
