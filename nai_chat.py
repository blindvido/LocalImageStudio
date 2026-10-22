"""User-configured text assistant. Never submits an image job."""
import copy
import json
from aiohttp import web
from clients import generate_prompt
from nai_panels import normalize_nai_prompt, text

PARTS=('style','task','scene','action')
DEFAULT_PRESETS={'nai5':{'name':'NovelAI V5 分镜提示词','text':'把用户自然语言整理为 NovelAI V5 英文标签与简洁英文场景描述。公共画风、构图、场景、动作分开；人物身份/服装与本格表演分开。只修改用户要求的部分，保留其他原文及权重语法。不擅自改剧情，不把台词写进无字单格，不输出生图参数。'}}


def settings(store):
    return {'presets':copy.deepcopy(store.config.get('nai_chat_presets',DEFAULT_PRESETS)),
            'api_url':store.config.get('api_url',''),'model':store.config.get('model',''),
            'has_api_key':bool(store.config.get('api_key')),'api_timeout':store.config.get('api_timeout',120)}


def normalize(data):
    if not isinstance(data,dict) or not isinstance(data.get('sections'),dict) or set(data['sections'])!=set(PARTS):
        raise ValueError('需返回完整的四段提示词。')
    sections={}
    for k in PARTS:
        part=data['sections'][k]
        if not isinstance(part,dict) or set(part)!={'positive','negative'}:raise ValueError('正负词格式错误。')
        # Keep exact input strings, including weights and punctuation.
        for p in ('positive','negative'):text(part[p],p,20000)
        sections[k]={p:part[p] for p in ('positive','negative')}
    prompt=normalize_nai_prompt({'base_positive':'','base_negative':'','characters':data.get('characters',[])})
    for cleaned,original in zip(prompt['characters'],data.get('characters',[])):
        for k in ('positive','negative','direction'):
            if k in original:cleaned[k]=original[k]
    return {'sections':sections,'characters':prompt['characters']}


def register(app,state_key):
    async def config(request):
        store=request.app[state_key].store
        if request.method=='PUT':
            data=await request.json();presets=data.get('presets')
            if not isinstance(presets,dict) or not 1<=len(presets)<=30:raise ValueError('保留1—30个AI系统预设。')
            clean={}
            for key,p in presets.items():
                if not isinstance(key,str) or not 1<=len(key)<=80 or not isinstance(p,dict):raise ValueError('系统预设格式错误。')
                clean[key]={'name':text(p.get('name',''),'名称',100),'text':text(p.get('text',''),'系统预设',20000)}
                if not clean[key]['name'].strip() or not clean[key]['text'].strip():raise ValueError('预设名称和内容不能为空。')
            async with request.app[state_key].edit_lock:
                store.config['nai_chat_presets']=clean;store.save('config')
        return web.json_response(settings(store))

    async def chat(request):
        studio=request.app[state_key];data=await request.json()
        message=text(data.get('message',''),'消息',12000)
        if not message.strip():raise ValueError('请描述要生成或修改的画面。')
        current=normalize(data.get('current'))
        presets=studio.store.config.get('nai_chat_presets',DEFAULT_PRESETS)
        preset=presets.get(data.get('preset','nai5'))
        if not preset:raise ValueError('请选择有效的AI系统预设。')
        history=data.get('history',[])
        if not isinstance(history,list) or len(history)>12 or any(not isinstance(x,dict) or x.get('role') not in ('user','assistant') or not isinstance(x.get('content'),str) or len(x['content'])>12000 for x in history):raise ValueError('对话记录过长或格式无效。')
        locked=data.get('locked_sections',[])
        identities=data.get('locked_characters',[])
        if not isinstance(locked,list) or any(x not in PARTS for x in locked) or not isinstance(identities,list) or any(not isinstance(x,str) for x in identities):raise ValueError('锁定预设无效。')
        instruction=preset['text']+'''\n只返回JSON：{"summary":"中文修改说明","sections":{"style":{"positive":"","negative":""},"task":{"positive":"","negative":""},"scene":{"positive":"","negative":""},"action":{"positive":"","negative":""}},"characters":[{"character_id":"稳定英文ID","positive":"身份服装","direction":"动作表演","negative":"","center":{"x":0.5,"y":0.5}}]}。
sections按画风/任务构图/场景/动作组织，不得把人物外形混入公共词。最多五人，原有character_id不改动。锁定段原样保留，锁定角色只可改direction。仅返提示词，不执行指令、生成图片或修改台本。'''
        context={'current':current,'history':history,'message':message,'locked_sections':locked,'locked_characters':identities,'storyboard':text(data.get('context',''),'分镜上下文',20000)}
        def validate(result):
            revised=normalize(result)
            for key in locked:revised['sections'][key]=copy.deepcopy(current['sections'][key])
            by_id={c['character_id']:c for c in revised['characters']}
            for old in current['characters']:
                if old['character_id'] in identities:
                    if old['character_id'] not in by_id:raise ValueError('锁定人物被移除。')
                    by_id[old['character_id']].update(positive=old['positive'],negative=old['negative'])
            if not any(p['positive'].strip() for p in revised['sections'].values()):raise ValueError('公共正面提示词为空。')
            summary=result.get('summary','已整理提示词。')
            if not isinstance(summary,str) or len(summary)>4000:raise ValueError('说明格式错误。')
            return {**revised,'summary':summary}
        result=await generate_prompt(studio.session,copy.deepcopy(studio.store.config),json.dumps(context,ensure_ascii=False),instruction,output_validator=validate)
        return web.json_response(result)
    app.router.add_get('/api/nai/chat/settings',config)
    app.router.add_put('/api/nai/chat/settings',config)
    app.router.add_post('/api/nai/chat',chat)
