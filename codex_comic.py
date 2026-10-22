"""Codex-led comic runs. No text-model calls; existing Studio owns generation.

context -> Codex reads/writes prompt -> submit -> collect -> human selection.
No automatic review or regeneration. Only human confirmation updates memory.
"""
import argparse
import hashlib
import json
import re
import uuid
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_cli import LocalAPI, download_images, wait_job


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def context(root, panel):
    if not re.fullmatch(r'P\d{2}\.\d+', panel):
        raise ValueError('Expected panel ID such as P03.1')
    memory = root / 'memory.md'
    script = root / 'chapters/ch01/script-v1.3/page-script.json'
    reference = root / 'chapters/ch01/page-art/gpt-image-v2/P02.png'
    pages = read(script)['pages']
    page = next(p for p in pages if any(x['id'] == panel for x in p['panels']))
    return {'panel_id': panel, 'memory': memory.read_text(encoding='utf-8'),
            'page': page, 'reference': str(reference),
            'hashes': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (memory, script, reference)}}


def check_retry(attempts, revision_request=None):
    if not attempts:
        return
    previous = read(attempts[-1] / 'record.json')
    if previous.get('state') != 'done':
        raise ValueError('Query the previous job first; do not repeat an uncertain submission.')
    if not revision_request or not revision_request.strip():
        raise ValueError('Another image requires an explicit human revision request; no auto-retry.')
    if any(read(p / 'record.json').get('confirmed') for p in attempts):
        raise ValueError('Confirmed result is protected; create a separate panel version.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['context', 'submit', 'collect', 'confirm'])
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--panel', required=True)
    parser.add_argument('--file', type=Path, help='Codex prompt specification JSON')
    parser.add_argument('--attempt', type=int, default=1)
    parser.add_argument('--human-confirmation', help='Exact human confirmation; never supplied by auto-review')
    parser.add_argument('--revision-request', help='Exact human request for another attempt; no auto-retry')
    args = parser.parse_args()
    root = args.project.resolve()
    ctx = context(root, args.panel)
    folder = root / 'codex-runs' / args.panel
    folder.mkdir(parents=True, exist_ok=True)
    if args.command == 'context':
        save(folder / 'context.json', ctx)
        print(json.dumps(ctx, ensure_ascii=False, indent=2))
        return
    attempt = folder / f'attempt-{args.attempt:02d}'
    if args.command == 'submit':
        # Context hashes bind the prompt to the materials Codex actually read.
        spec = read(args.file)
        if spec['context_hashes'] != ctx['hashes']:
            raise ValueError('Context changed; reread memory/script/reference and revise prompt.')
        attempts = sorted(folder.glob('attempt-*'))
        check_retry(attempts, args.revision_request)
        if args.attempt != len(attempts) + 1:
            raise ValueError('Attempt numbers must be consecutive.')
        api = LocalAPI('http://127.0.0.1:8190'); api.connect()
        panel = next(p for p in ctx['page']['panels'] if p['id'] == args.panel)
        values = {**spec['values'], 'MODEL_NAME': 'nai-diffusion-5-full'}
        saved = api.request('panels', 'POST', {
            'project': root.name + ' · Codex v1.3', 'chapter': '01',
            'page': str(ctx['page']['page']).zfill(2), 'panel': str(panel['panel']),
            'visible_description': panel['visual_brief'], 'nai_prompt': spec['nai_prompt'], 'values': values}) if not attempts else api.request('panels/' + read(attempts[0] / 'record.json')['panel_id'])
        if attempts:
            saved = api.request('panels/' + saved['id'], 'PUT', {
                'revision': saved['revision'], 'nai_prompt': spec['nai_prompt'], 'values': values})
        body = {'provider': 'novelai', 'panel_id': saved['id'], 'panel_revision': saved['revision'],
                'use_api': False, 'random_seed': False, 'request_id': str(uuid.uuid4())}
        if attempts:
            body['parent_job_id'] = read(attempts[-1] / 'record.json')['job_id']
        attempt.mkdir(exist_ok=False)
        save(attempt / 'context.json', ctx)
        save(attempt / 'request.json', body)
        save(attempt / 'prompt.json', spec)
        if args.revision_request:
            save(attempt / 'revision-request.json', {'human_request': args.revision_request})
        save(attempt / 'record.json', {'panel_id': saved['id'], 'state': 'submitting', 'confirmed': False})
        job = api.request('jobs', 'POST', body)
        save(attempt / 'job.json', job)
        save(attempt / 'record.json', {'panel_id': saved['id'], 'job_id': job['id'], 'state': job['state'], 'confirmed': False})
        print(json.dumps({'job_id': job['id'], 'attempt': str(attempt)}))
    elif args.command == 'collect':
        api = LocalAPI('http://127.0.0.1:8190'); api.connect()
        record = read(attempt / 'record.json')
        if 'job_id' not in record:
            request_id = read(attempt / 'request.json')['request_id']
            matches = [j for j in api.request('jobs') if j.get('request_id') == request_id]
            if not matches:
                raise ValueError('Submission unconfirmed; inspect service history, do not resubmit.')
            record['job_id'] = matches[0]['id']
        job = wait_job(api, api.request('jobs/' + record['job_id']), 45)
        save(attempt / 'job.json', job)
        record['state'] = job['state']
        if job['state'] == 'done':
            record['files'] = download_images(api, job, attempt)
        save(attempt / 'record.json', record)
        print(json.dumps(record, ensure_ascii=False))
    else:
        record = read(attempt / 'record.json')
        if not args.human_confirmation or not args.human_confirmation.strip() or record['state'] != 'done':
            raise ValueError('Human confirmation and a completed image are required.')
        memory = root / 'memory.md'
        marker = f'<!-- confirmed-job:{record["job_id"]} -->'
        contents = memory.read_text(encoding='utf-8')
        if marker not in contents:
            contents += f'\n{marker}\n- {args.panel}：人工确认 {args.human_confirmation}\n  - 任务：{record["job_id"]}\n  - 图像：{record["files"][0]}\n  - 完整提示词与参数：{attempt}\n'
            temp = memory.with_suffix('.tmp'); temp.write_text(contents, encoding='utf-8'); temp.replace(memory)
        api = LocalAPI('http://127.0.0.1:8190'); api.connect()
        panel = api.request('panels/' + record['panel_id'])
        api.request('panels/' + panel['id'], 'PUT', {'revision': panel['revision'], 'selected_job_id': record['job_id']})
        record['confirmed'] = True; record['human_confirmation'] = args.human_confirmation
        save(attempt / 'record.json', record)
        print('Human selection recorded.')


if __name__ == '__main__':
    main()
