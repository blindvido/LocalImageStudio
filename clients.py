import asyncio
import json
import re
import uuid
from urllib.parse import urlsplit
import aiohttp


class ComfyRejected(ValueError):
    """ComfyUI definitively rejected the submitted request."""


class SubmissionUnconfirmed(ValueError):
    """A submitted graph may have been accepted, but no ID was confirmed."""


def base_url(value):
    value = value.strip().rstrip('/')
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('请输入有效的 http/https 服务地址，不要包含密码、查询参数或锚点。')
    return value


def completion_url(value):
    value = base_url(value)
    return value if value.endswith('/chat/completions') else value + '/chat/completions'


async def list_models(session, config):
    url = base_url(config.get('api_url', ''))
    if url.endswith('/chat/completions'):
        url = url[:-len('/chat/completions')]
    headers = {'Authorization': 'Bearer ' + config['api_key']} if config.get('api_key') else {}
    try:
        async with session.get(url + '/models', headers=headers,
                               timeout=aiohttp.ClientTimeout(total=min(float(config.get('api_timeout', 120)), 30))) as response:
            if response.status >= 400:
                guidance = {401: '请检查密钥。', 403: '密钥没有模型列表权限。', 404: '服务未提供模型列表，请手动填写模型名称。', 429: '请求受限，请稍后重试。'}
                raise ValueError(f'获取模型列表返回 HTTP {response.status}。' + guidance.get(response.status, '请检查服务地址。'))
            data = await response.json(content_type=None)
    except asyncio.TimeoutError:
        raise ValueError('获取模型列表超时，请稍后重试或手动填写模型名称。') from None
    except aiohttp.ClientError:
        raise ValueError('无法连接模型列表接口，请检查地址、网络和证书。') from None
    except (ValueError, TypeError) as exc:
        if isinstance(exc, json.JSONDecodeError):
            raise ValueError('服务未返回有效的模型列表，请手动填写模型名称。') from None
        raise
    if not isinstance(data, dict) or not isinstance(data.get('data'), list):
        raise ValueError('服务未返回有效的模型列表，请手动填写模型名称。')
    models = sorted({item['id'].strip() for item in data['data'] if isinstance(item, dict)
                     and isinstance(item.get('id'), str) and item['id'].strip()}, key=str.casefold)
    if not models:
        raise ValueError('该连接未返回可用模型，请检查权限或手动填写模型名称。')
    return models


async def generate_prompt(session, config, description, template, image_url=None, *, output_validator=None):
    if not description.strip():
        raise ValueError('请先描述想要的画面。')
    if not config.get('api_url') or not config.get('model'):
        raise ValueError('请在设置中填写 API 地址和模型名称。')
    headers = {'Authorization': 'Bearer ' + config['api_key']} if config.get('api_key') else {}
    content = description if image_url is None else [
        {'type': 'text', 'text': description}, {'type': 'image_url', 'image_url': {'url': image_url}}]
    body = {'model': config['model'], 'messages': [{'role': 'system', 'content': template},
                                                  {'role': 'user', 'content': content}], 'stream': False}
    try:
        async with session.post(completion_url(config['api_url']), json=body, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=float(config.get('api_timeout', 120)))) as response:
            if response.status >= 400:
                guidance = {401: '请检查 API 密钥。', 403: '请检查访问权限。', 404: '请检查 API 地址和模型名称。', 429: '额度或速率受限，请稍后重试。'}
                raise ValueError(f'Prompt API 返回 HTTP {response.status}。' + guidance.get(response.status, '请检查 API 服务。'))
            data = await response.json(content_type=None)
    except asyncio.TimeoutError:
        raise ValueError('Prompt API 请求超时，未自动重试。') from None
    except aiohttp.ClientError:
        raise ValueError('无法连接 Prompt API，请检查地址、网络和证书。') from None
    choices = data.get('choices') if isinstance(data, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get('message')
    message = message if isinstance(message, dict) else {}
    content = message.get('content')
    refusal_text = isinstance(content, str) and re.match(
        r"^\s*(?:I (?:cannot|can't|am unable to) (?:fulfill|assist|help|generate|provide|comply)|(?:I'm sorry|I am sorry)[,， ]*(?:but )?I (?:cannot|can't|am unable to)|抱歉[,， ]*(?:我)?(?:无法|不能)|对不起[,， ]*(?:我)?(?:无法|不能))", content, re.I)
    if message.get('refusal') or choice.get('finish_reason') == 'content_filter' or refusal_text:
        raise ValueError('提示词 API 拒绝了本次请求，未生成绘图提示词。请检查人物库的固定外形、默认服装和本次描述是否包含服务商限制的内容，调整后再试。')
    if choice.get('finish_reason') in {'length', 'max_tokens'}:
        raise ValueError('提示词 API 回复被截断，未返回完整提示词。请精简描述或人物设定，或检查服务商的输出长度限制。未自动重试。')
    try:
        content = data['choices'][0]['message']['content'].strip()
        content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content, flags=re.I)
        result = json.loads(content)
        if output_validator is not None:
            return output_validator(result)
        if not isinstance(result.get('positive_prompt'), str) or not result['positive_prompt'].strip() or not isinstance(result.get('negative_prompt'), str):
            raise ValueError()
        return {key: result[key].strip() for key in ('positive_prompt', 'negative_prompt')}
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        if output_validator is not None:
            raise ValueError('NAI 提示词草稿格式或人物绑定错误，原稿未修改；请检查草稿服务或手动编辑。') from None
        raise ValueError('API 输出格式不正确：需要 positive_prompt 和 negative_prompt 两个字符串字段。可调整模板或手动输入提示词。') from None


class ComfyClient:
    def __init__(self, session, url, client_id=None):
        self.session = session
        self.url = base_url(url)
        self.client_id = client_id or str(uuid.uuid4())

    async def request(self, method, path, **kwargs):
        try:
            async with self.session.request(method, self.url + path, timeout=aiohttp.ClientTimeout(total=30), **kwargs) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    error = data.get('error', {})
                    message = error.get('message', str(error)) if isinstance(error, dict) else str(error)
                    details = json.dumps(data.get('node_errors', {}), ensure_ascii=False)
                    raise ComfyRejected(f'ComfyUI HTTP {response.status}：{message} {details}'[:2500])
                return data
        except asyncio.TimeoutError:
            raise ValueError('ComfyUI 请求超时，未自动重新提交。请检查 ComfyUI 队列。') from None
        except aiohttp.ClientError:
            raise ValueError('无法连接 ComfyUI，请检查地址或启动 ComfyUI。') from None
        except (json.JSONDecodeError, TypeError):
            raise ValueError('ComfyUI 返回了无效响应，请检查服务地址。') from None

    async def submit(self, graph):
        try:
            result = await self.request('POST', '/prompt', json={'prompt': graph, 'client_id': self.client_id})
            if not isinstance(result, dict) or not result.get('prompt_id'):
                raise ValueError('ComfyUI 未返回任务 ID。')
            return result['prompt_id']
        except ComfyRejected:
            raise
        except ValueError as exc:
            raise SubmissionUnconfirmed(str(exc) + ' 提交结果未确认，请先检查 ComfyUI 队列，勿重复提交。') from None

    async def upload_image(self, content, filename):
        form = aiohttp.FormData()
        form.add_field('image', content, filename=filename, content_type='image/png')
        result = await self.request('POST', '/upload/image', data=form)
        name, folder = result.get('name'), result.get('subfolder', '')
        if not isinstance(name, str) or not name or result.get('type') != 'input':
            raise ValueError('ComfyUI 未确认图片上传。')
        return '/'.join(x for x in (folder, name) if x)

    async def history(self, prompt_id):
        return (await self.request('GET', '/history/' + prompt_id)).get(prompt_id)

    @staticmethod
    def images(history):
        if not history:
            return []
        if history.get('status', {}).get('status_str') == 'error':
            messages = history.get('status', {}).get('messages', [])
            error = next((entry[1] for entry in reversed(messages) if entry[0] == 'execution_error'), {})
            raise ValueError(f"ComfyUI 执行失败（节点 {error.get('node_id', '?')}）：{error.get('exception_message', '请查看 ComfyUI 日志')}"[:2500])
        return [image for output in history.get('outputs', {}).values() for image in output.get('images', [])]

    async def options(self):
        info = await self.request('GET', '/object_info')
        selectors = {'MODEL_NAME': [('CheckpointLoaderSimple', 'ckpt_name'), ('UNETLoader', 'unet_name')],
                     'clip': [('CLIPLoader', 'clip_name')], 'vae': [('VAELoader', 'vae_name')],
                     'sampler_name': [('KSampler', 'sampler_name')], 'scheduler': [('KSampler', 'scheduler')]}
        choices = {}
        for name, sources in selectors.items():
            values = []
            for node, field in sources:
                spec = info.get(node, {}).get('input', {}).get('required', {}).get(field, [])
                if spec and isinstance(spec[0], list):
                    values.extend(spec[0])
            choices[name] = list(dict.fromkeys(values))
        return {'choices': choices, 'node_types': list(info)}
