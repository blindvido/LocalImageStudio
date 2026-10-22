"""Agent interface: local JSON requests, no API keys exposed to the caller."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
TERMINAL = {'done', 'failed', 'unknown', 'interrupted'}


def read_request(path):
    result = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(result, dict):
        raise ValueError('请求文件须为 JSON 对象。')
    return result


def choose_workflow(workflows, selector):
    exact = [w for w in workflows if w['id'] == selector]
    matches = exact or [w for w in workflows if w['name'] == selector]
    if len(matches) == 1:
        return matches[0]['id']
    if len(matches) > 1:
        raise ValueError('多个工作流同名，请使用工作流 ID。')
    raise ValueError('请明确选择工作流 ID 或完整名称；使用 workflows 命令查看。')


def compact_job(job):
    result = {key: value for key, value in job.items() if key not in {'workflow', 'client_id'}}
    result['workflow_name'] = job.get('workflow', {}).get('name', '')
    result['image_urls'] = [f"/api/images/{job['id']}/{index}" for index in range(len(job.get('images', [])))]
    return result


class LocalAPI:
    def __init__(self, url):
        self.url = url.rstrip('/')
        parsed = urlsplit(self.url)
        if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'} or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('Agent 接口仅支持本机 http://127.0.0.1:端口 地址。')
        self.token = ''

    def request(self, path, method='GET', body=None, binary=False):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        request = Request(self.url + '/api/' + path, data=data, method=method,
                          headers={'Content-Type': 'application/json', 'X-Studio-Token': self.token})
        try:
            with urlopen(request, timeout=620 if path in {'prompt', 'edit/prompt'} else 150) as response:
                result = response.read()
                return result if binary else json.loads(result)
        except HTTPError as exc:
            try:
                message = json.loads(exc.read()).get('error', f'HTTP {exc.code}')
            except ValueError:
                message = f'HTTP {exc.code}'
            raise ValueError(message) from None
        except (URLError, TimeoutError, ConnectionError):
            raise ValueError('本地绘图服务未连接或请求超时。用 start 启动；已提交请求不要重复提交，请查询历史。') from None

    def connect(self):
        self.token = self.request('bootstrap')['token']

    def start(self):
        try:
            self.connect()
            return {'started': False, 'url': self.url, 'message': '本地绘图服务已运行。'}
        except ValueError:
            pass
        log_dir = ROOT / 'data' / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        parsed = urlsplit(self.url)
        python = ROOT.parent / 'python_embeded' / 'python.exe'
        if not python.is_file():
            python = Path(sys.executable)
        with (log_dir / 'studio.log').open('ab') as log:
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            process = subprocess.Popen([str(python), '-X', 'utf8', str(ROOT / 'studio_server.py'), '--port', str(parsed.port or 8190)], cwd=ROOT, stdout=log, stderr=log, creationflags=flags)
        for _ in range(50):
            time.sleep(0.2)
            try:
                self.connect()
                return {'started': True, 'pid': process.pid, 'url': self.url}
            except ValueError:
                if process.poll() is not None:
                    break
        raise ValueError('服务启动失败，请检查 local-image-studio/data/logs/studio.log。')


def wait_job(api, job, seconds):
    until = time.monotonic() + seconds
    while job['state'] not in TERMINAL and time.monotonic() < until:
        time.sleep(min(2, max(0, until - time.monotonic())))
        job = api.request('jobs/' + job['id'])
    return job


def download_images(api, job, directory):
    if job['state'] != 'done':
        raise ValueError('任务尚未成功完成，不能下载图片。')
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, image in enumerate(job['images']):
        suffix = Path(image['filename']).suffix.lower()
        if suffix not in {'.png', '.jpg', '.jpeg', '.webp', '.gif'}:
            suffix = '.png'
        path = directory / f"{job['id']}-{index + 1}{suffix}"
        path.write_bytes(api.request(f"images/{job['id']}/{index}", binary=True))
        paths.append(str(path))
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description='绘境本地 Agent 接口（输出 JSON）')
    parser.add_argument('--url', default='http://127.0.0.1:8190')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('start', help='启动本地绘图服务，不打开浏览器')
    sub.add_parser('status', help='检查服务、API 配置、ComfyUI')
    sub.add_parser('panel-list', help='列出已保存的单格')
    panel_get = sub.add_parser('panel-get', help='读取单格及人物快照'); panel_get.add_argument('id')
    panel_save = sub.add_parser('panel-save', help='新建或保存单格'); panel_save.add_argument('--request-file', required=True); panel_save.add_argument('--id')
    for name in ('nai-preview', 'nai-draft'):
        command = sub.add_parser(name, help='预览 NAI 请求或生成独立人物提示词草稿'); command.add_argument('--request-file', required=True)
    listing = sub.add_parser('workflows', help='列出可选工作流')
    listing.add_argument('--detail', action='store_true')
    upload = sub.add_parser('edit-upload', help='上传改图原图，返回 edit_id')
    upload.add_argument('--image', required=True, help='本地图片路径')
    session = sub.add_parser('edit-session', help='读取改图会话和最新任务 ID')
    session.add_argument('id')
    for name in ['edit-select', 'edit-prompt', 'edit-render']:
        command = sub.add_parser(name, help='半自动选区、改图提示词或局部重绘')
        command.add_argument('--request-file', required=True)
        if name != 'edit-prompt':
            command.add_argument('--wait', type=float, default=0)
            command.add_argument('--output')
    for name in ['prompt', 'generate']:
        command = sub.add_parser(name, help='只生成提示词' if name == 'prompt' else '提交生图任务')
        command.add_argument('--request-file', required=True, help='UTF-8 JSON 请求文件')
        if name == 'generate':
            command.add_argument('--wait', type=float, default=0, help='最多等待 55 秒，之后用 job 继续查询')
            command.add_argument('--output', help='成功时下载图片到指定目录')
    job_parser = sub.add_parser('job', help='查询已有任务，绝不重新提交')
    job_parser.add_argument('id'); job_parser.add_argument('--wait', type=float, default=0); job_parser.add_argument('--output')
    sub.add_parser('history', help='列出历史任务及 ID')
    resume = sub.add_parser('resume', help='恢复查询未知任务，不重新提交')
    resume.add_argument('id')
    download = sub.add_parser('download', help='下载已完成任务的图片')
    download.add_argument('id'); download.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if hasattr(args, 'wait') and not 0 <= args.wait <= 55:
            raise ValueError('--wait 须在 0–55 秒之间。')
        api = LocalAPI(args.url)
        if args.command == 'start':
            result = api.start()
        else:
            api.connect()
            if args.command == 'status':
                config = api.request('config')
                result = {'studio': True, 'api_configured': bool(config['api_url'] and config['model']), 'model': config['model'], 'comfy_url': config['comfy_url'], 'novelai_usage': api.request('nai/usage')}
                try:
                    result['comfy'] = api.request('comfy/status')
                except ValueError as exc:
                    result['comfy'] = {'connected': False, 'error': str(exc)}
            elif args.command == 'workflows':
                workflows = api.request('workflows')
                result = workflows if args.detail else [{key:w[key] for key in ['id','name','values','mappings','template']} | {'variables':w['inspection']['variables']} for w in workflows]
            elif args.command == 'panel-list':
                result = api.request('panels')
            elif args.command == 'panel-get':
                result = api.request('panels/' + args.id)
            elif args.command == 'panel-save':
                result = api.request('panels/' + args.id if args.id else 'panels', 'PUT' if args.id else 'POST', read_request(args.request_file))
            elif args.command in {'nai-preview', 'nai-draft'}:
                result = api.request('nai/preview' if args.command == 'nai-preview' else 'nai/prompt-draft', 'POST', read_request(args.request_file))
            elif args.command == 'edit-upload':
                image = Path(args.image)
                if image.stat().st_size > 20 * 1024 * 1024:
                    raise ValueError('图片须小于 20 MB。')
                result = api.request('edit/upload', 'POST', {'image_base64': base64.b64encode(image.read_bytes()).decode('ascii')})
            elif args.command == 'edit-session':
                result = api.request('edit/' + args.id)
            elif args.command in {'edit-select', 'edit-prompt', 'edit-render'}:
                body = read_request(args.request_file)
                result = api.request('edit/' + args.command.removeprefix('edit-'), 'POST', body)
                if args.command != 'edit-prompt':
                    job = wait_job(api, result, args.wait)
                    result = compact_job(job)
                    if args.output and job['state'] == 'done':
                        result['files'] = download_images(api, job, args.output)
            elif args.command in {'prompt', 'generate'}:
                body = read_request(args.request_file)
                if 'panel_id' in body or 'nai_prompt' in body:
                    body.setdefault('provider', 'novelai'); body.setdefault('use_api', False)
                if body.get('provider') != 'novelai':
                    body['workflow_id'] = choose_workflow(api.request('workflows'), body.get('workflow_id') or body.get('workflow'))
                if args.command == 'prompt':
                    result = api.request('prompt', 'POST', body)
                else:
                    body.setdefault('use_api', True)
                    job = wait_job(api, api.request('jobs','POST',body), args.wait)
                    result = compact_job(job)
                    if args.output and job['state'] == 'done':
                        result['files'] = download_images(api, job, args.output)
            elif args.command in {'job', 'download', 'resume'}:
                job = api.request('jobs/' + args.id + '/resume', 'POST', {}) if args.command == 'resume' else api.request('jobs/' + args.id)
                if args.command == 'job':
                    job = wait_job(api, job, args.wait)
                result = compact_job(job)
                if getattr(args, 'output', None) and job['state'] == 'done':
                    result['files'] = download_images(api, job, args.output)
                elif args.command == 'download':
                    raise ValueError('任务尚未成功完成。')
            else:
                result = [compact_job(job) for job in api.request('jobs')]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if isinstance(result, dict) and result.get('state') in {'failed','interrupted'} else 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
