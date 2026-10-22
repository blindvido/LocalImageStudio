"""Validated five-character prompt contract shared by UI, CLI and NAI adapter."""
import math


def text(value, label, limit=12000):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'{label} 须为不超过 {limit} 字的文本。')
    return value.strip()


def normalize_nai_prompt(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError('NAI 提示词须为对象。')
    if set(data) - {'base_positive', 'base_negative', 'characters'}:
        raise ValueError('NAI 提示词含不支持的字段；参考图目前仅供查看。')
    chars = data.get('characters', [])
    if not isinstance(chars, list) or len(chars) > 5:
        raise ValueError('每格最多使用五个人物槽。')
    result = {'base_positive': text(data.get('base_positive', ''), '全局正面词'),
              'base_negative': text(data.get('base_negative', ''), '全局负面词'), 'characters': []}
    ids = set()
    for char in chars:
        if not isinstance(char, dict) or set(char) - {'character_id', 'positive', 'negative', 'direction', 'center'}:
            raise ValueError('人物提示词格式无效。')
        key = text(char.get('character_id', ''), '人物 ID', 100)
        if not key or key in ids:
            raise ValueError('人物 ID 不能为空或重复。')
        ids.add(key)
        center = char.get('center', {'x': .5, 'y': .5})
        if not isinstance(center, dict) or set(center) != {'x', 'y'}:
            raise ValueError('人物坐标须包含 x 和 y。')
        for n in center.values():
            if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or not 0 <= n <= 1:
                raise ValueError('人物坐标须为 0–1 的有限数值。')
        positive = text(char.get('positive', ''), '人物正面词')
        if not positive:
            raise ValueError('启用的人物槽须填写人物提示词。')
        character = {'character_id': key, 'positive': positive,
                     'negative': text(char.get('negative', ''), '人物负面词'),
                     'center': dict(center)}
        direction = text(char.get('direction', ''), '人物本格表演')
        if direction:
            character['direction'] = direction
        result['characters'].append(character)
    if chars and any('|' in x for x in [result['base_positive'], result['base_negative'], *[c[k] for c in result['characters'] for k in ('positive', 'negative')]]):
        raise ValueError('独立人物槽不能与 | 分段语法混用。')
    return result


def captions(characters, model, field):
    def coordinate(n):
        # V4/4.5 UI uses centers of five equally spaced grid cells.
        return round((min(4, int(n * 5)) + .5) / 5, 1) if 'diffusion-5' not in model else n
    return [{'char_caption': ', '.join(x for x in (c[field], c.get('direction', '') if field == 'positive' else '') if x),
             'centers': [{k: coordinate(v) for k, v in c['center'].items()}]} for c in characters]
