"""Single-panel persistence validation. Private notes never enter generation input."""
import copy
import uuid
from datetime import datetime
import novelai
from nai_panels import normalize_nai_prompt, text


def clean_panel(data, records, existing=None):
    if not isinstance(data, dict):
        raise ValueError('单格资料须为对象。')
    merged = {**(existing or {}), **data}
    result = {'id': existing['id'] if existing else str(uuid.uuid4()), 'schema_version': 1,
              'revision': existing['revision'] + 1 if existing else 1}
    for field, limit in [('project', 100), ('chapter', 40), ('page', 40), ('panel', 40), ('script_version', 100), ('visible_description', 12000), ('private_notes', 12000), ('title', 200), ('camera_notes', 4000), ('lettering_notes', 4000)]:
        result[field] = text(merged.get(field, ''), field, limit)
    dialogue = merged.get('dialogue', '')
    if not isinstance(dialogue, (str, list)) or len(str(dialogue)) > 20000:
        raise ValueError('对白须为文本或列表，最多20000字符。')
    result['dialogue'] = copy.deepcopy(dialogue)
    modules = merged.get('modules')
    if modules is not None:
        if not isinstance(modules, dict) or set(modules) - {'style','scene','camera','action'}:
            raise ValueError('提示词模块须为画风、场景、镜头、动作。')
        if any(not isinstance(v,dict) for v in modules.values()): raise ValueError('模块须包含正面和负面文本。')
        result['modules'] = {k: {f: text(modules.get(k, {}).get(f, ''), f, 12000) for f in ('positive','negative')} for k in ('style','scene','camera','action')}
    result['nai_prompt'] = normalize_nai_prompt(merged.get('nai_prompt', {}))
    values = merged.get('values', {})
    if not isinstance(values, dict) or set(values) - set(novelai.DEFAULTS):
        raise ValueError('单格含不支持的生图参数。')
    result['values'] = {**novelai.DEFAULTS, **values}
    novelai.payload(result['nai_prompt']['base_positive'] or 'pending', result['nai_prompt']['base_negative'], result['values'], characters=result['nai_prompt']['characters'])
    old = {r['id']: r for r in (existing or {}).get('character_snapshots', [])}
    snapshots = []
    for char in result['nai_prompt']['characters']:
        record = old.get(char['character_id']) or records.get(char['character_id'])
        if not record:
            raise ValueError('人物已不存在，请从人物库重新选择；已保存快照的人物仍可使用。')
        snapshots.append({k: copy.deepcopy(record.get(k, '')) for k in ('id', 'name', 'appearance', 'clothing', 'negative_prompt')})
    result['character_snapshots'] = snapshots
    result['selected_job_id'] = text(merged.get('selected_job_id', ''), '选定任务 ID', 100)
    result['created_at'] = (existing or {}).get('created_at', datetime.now().astimezone().isoformat())
    result['updated_at'] = datetime.now().astimezone().isoformat()
    return result


def visible_snapshot(panel):
    return copy.deepcopy({k: panel[k] for k in ('id', 'revision', 'project', 'chapter', 'page', 'panel', 'visible_description', 'nai_prompt', 'values', 'character_snapshots')})
