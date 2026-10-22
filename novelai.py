"""NovelAI protocol adapter, based on official schema and local st-chatu8 conventions."""
import asyncio
import base64
import io
import math
import zipfile
import aiohttp
from PIL import Image
from clients import base_url, SubmissionUnconfirmed
from nai_panels import normalize_nai_prompt, captions

DEFAULTS = {'width':832, 'height':1216, 'steps':28, 'cfg_scale':5, 'seed':0,
            'MODEL_NAME':'nai-diffusion-5-full', 'sampler_name':'k_euler_ancestral',
            'scheduler':'karras', 'cfg_rescale':0}
MODELS = ['nai-diffusion-5-full','nai-diffusion-5-curated','nai-diffusion-4-5-full',
          'nai-diffusion-4-5-curated','nai-diffusion-4-full','nai-diffusion-4-curated-preview','nai-diffusion-3']
SAMPLERS = ['k_euler_ancestral','k_euler','k_dpmpp_2m','k_dpmpp_sde','k_dpmpp_2s_ancestral','ddim_v3']
SCHEDULES = ['karras','native','exponential','polyexponential']

def fixed_prompts(config):
    return {key: config.get('novelai_' + key, '').strip() for key in ('fixed_positive', 'fixed_negative')}


def prepend_fixed(fixed, prompt):
    fixed, prompt = fixed.strip(), prompt.strip()
    if not fixed or prompt == fixed or prompt.startswith(fixed + ', '):
        return prompt
    return ', '.join(x for x in (fixed, prompt) if x)


def apply_fixed(config, positive, negative):
    fixed = fixed_prompts(config)
    return (prepend_fixed(fixed['fixed_positive'], positive),
            prepend_fixed(fixed['fixed_negative'], negative))


def endpoint(url):
    url = base_url(url)
    return url if url.endswith('/ai/generate-image') else url + '/ai/generate-image'

def payload(positive, negative, values, *, characters=None, img2img=None):
    if not isinstance(values, dict):
        raise ValueError('NovelAI 参数须为对象。')
    if any('reference' in k.lower() or k in {'image', 'strength', 'noise', 'action'} for k in values):
        raise ValueError('此入口暂不支持参考图条件或图生图；参考图仅供查看。')
    chars = normalize_nai_prompt({'base_positive': positive, 'base_negative': negative, 'characters': characters or []})['characters']
    v = {**DEFAULTS, **values}
    def number(key, low, high, integer=False):
        n = v[key]
        if isinstance(n, bool) or not isinstance(n, (int,float)) or not math.isfinite(n) or not low <= n <= high or (integer and n != int(n)):
            raise ValueError(f'NovelAI 参数 {key} 无效，范围为 {low}–{high}。')
        return int(n) if integer else float(n)
    width, height = number('width',64,2048,True), number('height',64,2048,True)
    if width % 64 or height % 64 or width * height > 3145728:
        raise ValueError('NovelAI 宽高须为64的倍数，总像素不超过3145728。')
    if not isinstance(positive,str) or not positive.strip() or not isinstance(negative,str):
        raise ValueError('请填写有效的正负提示词。')
    if v['MODEL_NAME'] not in MODELS or v['sampler_name'] not in SAMPLERS or v['scheduler'] not in SCHEDULES:
        raise ValueError('请选择支持的 NovelAI 模型、采样器和噪声计划。')
    model = v['MODEL_NAME']
    if chars and model == 'nai-diffusion-3':
        raise ValueError('所选模型不支持独立人物提示词，请使用 V4 或以上模型。')
    p = {'params_version':4 if 'diffusion-5' in model else 3, 'width':width,'height':height,
         'steps':number('steps',1,50,True),'scale':number('cfg_scale',0,10),
         'seed':number('seed',0,2**32-1,True),'sampler':v['sampler_name'],
         'noise_schedule':v['scheduler'],'cfg_rescale':number('cfg_rescale',0,1),
         'n_samples':1,'negative_prompt':negative,'ucPreset':3,'qualityToggle':False,
         'dynamic_thresholding':False,'sm':False,'sm_dyn':False,'legacy':False,
         'deliberate_euler_ancestral_bug':False,'prefer_brownian':True}
    if model != 'nai-diffusion-3':
        p['v4_prompt'] = {'caption':{'base_caption':positive,'char_captions':captions(chars, model, 'positive')},'use_coords':bool(chars),'use_order':True}
        p['v4_negative_prompt'] = {'caption':{'base_caption':negative,'char_captions':captions(chars, model, 'negative')},'legacy_uc':False}
    action = 'generate'
    if img2img is not None:
        strength = img2img['strength']
        if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1:
            raise ValueError('Strength 须为 0–1 之间的数字。')
        if not isinstance(img2img.get('image'), bytes):
            raise ValueError('重绘底图无效。')
        action = 'img2img'
        p.update(image=base64.b64encode(img2img['image']).decode('ascii'), strength=strength, noise=0, extra_noise_seed=p['seed'])
    return {'input':positive,'model':model,'action':action,'parameters':p}

def unpack(content):
    if len(content) > 40*1024*1024:
        raise ValueError('NovelAI 图片响应过大。')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            entries = [i for i in z.infolist() if not i.is_dir() and i.filename.lower().endswith('.png')]
            if len(entries) != 1 or entries[0].file_size > 30*1024*1024:
                raise ValueError('NovelAI 未返回预期的单张 PNG 图片。')
            data = z.read(entries[0])
        with Image.open(io.BytesIO(data)) as img:
            if img.format != 'PNG' or img.width*img.height > 3145728:
                raise ValueError('NovelAI 返回了无效图片。')
            img.verify()
        return data
    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError):
        raise ValueError('NovelAI 返回的图片压缩包无效；未重新提交。') from None

async def generate(session, config, positive, negative, values, *, characters=None, img2img=None):
    if not config.get('novelai_token'):
        raise ValueError('请先在连接与设置填写 NovelAI Token。')
    body = payload(positive, negative, values, characters=characters, img2img=img2img)
    try:
        async with session.post(endpoint(config['novelai_url']), json=body,
                                headers={'Authorization':'Bearer '+config['novelai_token'],'Accept':'application/zip'},
                                timeout=aiohttp.ClientTimeout(total=300), allow_redirects=False) as r:
            if not 200 <= r.status < 300:
                reason = {401:'Token 无效。',403:'权限不足或访问被阻止。',402:'额度不足。',429:'请求受限；未自动重试。',400:'请检查模型和参数。'}.get(r.status,'请检查服务状态和接口地址。')
                raise ValueError(f'NovelAI HTTP {r.status}：{reason}')
            data = bytearray()
            async for chunk in r.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data)>40*1024*1024: raise ValueError('NovelAI 图片响应过大；未重新提交。')
            return unpack(bytes(data))
    except (asyncio.TimeoutError, aiohttp.ClientError):
        raise SubmissionUnconfirmed('NovelAI 请求结果未确认，请在服务商处检查额度与记录；未自动重试。') from None

async def test_connection(session, config):
    if not config.get('novelai_token'):raise ValueError('请填写 NovelAI Token。')
    url = endpoint(config['novelai_url']) + '/suggest-tags'
    try:
        async with session.get(url,params={'model':config['novelai_model'],'prompt':'flower'},
                               headers={'Authorization':'Bearer '+config['novelai_token']},
                               timeout=aiohttp.ClientTimeout(total=30),allow_redirects=False) as r:
            if r.status != 200:raise ValueError(f'NovelAI 连接测试 HTTP {r.status}。请检查 Token、地址和模型；中转服务可能不支持标签查询。')
            d = await r.json(content_type=None)
            if not isinstance(d,(dict,list)):raise ValueError('NovelAI 标签接口返回格式异常。')
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise ValueError('无法连接 NovelAI 标签接口，请检查网络和地址。') from None
    return {'message':'标签查询连接成功，未进行生图。实际出图权限与额度需生成图片验证。'}
