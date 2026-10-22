"""Literal work labels for history; inference never changes generation records."""
import re
from aiohttp import web


def clean_tags(value):
    if not isinstance(value, list) or len(value) > 10:
        raise ValueError('作品标签须为列表，最多10个。')
    result, seen = [], set()
    for tag in value:
        if not isinstance(tag, str) or not tag.strip() or len(tag.strip()) > 40:
            raise ValueError('每个标签须为1—40字。')
        tag = tag.strip()
        if tag.casefold() not in seen:
            seen.add(tag.casefold()); result.append(tag)
    return result


def effective_tags(job):
    if 'work_tags' in job:
        return job['work_tags']
    parts = [job.get('description', ''), job.get('positive_prompt', '')]
    nai = job.get('nai_prompt') or {}
    parts.append(nai.get('base_positive', ''))
    parts.extend(c.get('positive', '') for c in nai.get('characters', []))
    text = '\n'.join(str(p) for p in parts).casefold()
    names = [('绝区零', r'绝区零|\bzenless[ _]+zone[ _]+zero\b'),
             ('无职转生', r'无职转生|無職転生|\bmushoku[ _]+tensei\b')]
    return [name for name, pattern in names if re.search(pattern, text)]


def tagged(job):
    return {**job, 'work_tags': effective_tags(job), 'work_tags_source': 'manual' if 'work_tags' in job else 'inferred'}


def register(app, state_key):
    async def save(request):
        studio = request.app[state_key]
        data = await request.json()
        if not isinstance(data, dict) or set(data) != {'tags'}:
            raise ValueError('请仅提供tags字段。')
        tags = clean_tags(data['tags'])
        async with studio.edit_lock:
            job = studio.store.jobs.get(request.match_info['id'])
            if not job: raise web.HTTPNotFound()
            job['work_tags'] = tags
            studio.persist_job(job)
        return web.json_response(tagged(job))
    app.router.add_put('/api/jobs/{id}/work-tags', save)
