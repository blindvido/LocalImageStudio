"""Local prompt text records. Never normalize, tokenize or call a language model."""
import uuid
from aiohttp import web


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('预设须为对象。')
    if not isinstance(data.get('name'), str) or not data['name'].strip() or len(data['name']) > 100:
        raise ValueError('预设名称须为非空文本，最多100字符。')
    positive, negative = data.get('text', ''), data.get('negative_text', '')
    if any(not isinstance(value, str) or len(value) > 12000 for value in (positive, negative)):
        raise ValueError('正负提示词须为文本，每项最多12000字符。')
    if not positive.strip() and not negative.strip():
        raise ValueError('正面或负面提示词至少填写一项。')
    if data.get('category') not in ('style', 'task', 'scene', 'character', 'action'):
        raise ValueError('预设分类无效。')
    return {'name':data['name'], 'category':data['category'], 'text':positive, 'negative_text':negative}



def register(app, state_key):
    async def collection(request):
        store = request.app[state_key].store
        if request.method == 'GET':
            return web.json_response(list(store.prompt_presets.values()))
        record = {'id': str(uuid.uuid4()), **validate(await request.json())}
        store.prompt_presets[record['id']] = record
        store.save('prompt_presets')
        return web.json_response(record)

    async def item(request):
        store = request.app[state_key].store
        key = request.match_info['id']
        if key not in store.prompt_presets:
            raise web.HTTPNotFound()
        if request.method == 'DELETE':
            del store.prompt_presets[key]
            store.save('prompt_presets')
            return web.json_response({'deleted': True})
        record = {'id': key, **validate(await request.json())}
        store.prompt_presets[key] = record
        store.save('prompt_presets')
        return web.json_response(record)

    app.router.add_get('/api/prompt-presets', collection)
    app.router.add_post('/api/prompt-presets', collection)
    app.router.add_put('/api/prompt-presets/{id}', item)
    app.router.add_delete('/api/prompt-presets/{id}', item)
