import copy
import json
from pathlib import Path

TEMPLATES = {
    'tags': {'name': '标签式 · SDXL / Illustrious', 'text': '你是专业绘图提示词助手。将用户描述转换为英文逗号分隔的绘图标签，忠实保留主体、人数、动作、构图、环境与风格。不要擅自增加角色。返回 JSON 对象，键为 positive_prompt 和 negative_prompt，值均为字符串。negative_prompt 描述需要避免的画面问题。不要解释，不要代码块。'},
    'natural': {'name': '自然语言式 · Flux / Anima', 'text': '你是专业绘图提示词助手。根据用户描述编写清晰的英文画面描述，使用连贯自然语言，包含主体、动作、构图、光线、环境与风格，忠实保留用户意图。返回 JSON 对象，键为 positive_prompt 和 negative_prompt，值均为字符串。如果模型不需要负面提示词可返回空字符串。不要解释，不要代码块。'},
}
DEFAULT_CONFIG = {'api_url': '', 'api_key': '', 'model': '', 'comfy_url': 'http://127.0.0.1:8188',
                  'api_timeout': 120, 'templates': TEMPLATES, 'image_provider':'comfyui',
                  'novelai_fixed_positive':'', 'novelai_fixed_negative':'', 'novelai_url':'https://image.novelai.net', 'novelai_token':'', 'novelai_model':'nai-diffusion-5-full'}
DEFAULT_VALUES = {'width': 768, 'height': 1024, 'steps': 24, 'cfg_scale': 5, 'seed': 0,
                  'sampler_name': 'euler', 'scheduler': 'normal', 'MODEL_NAME': '', 'clip': '', 'vae': ''}


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.config = self.read('config', copy.deepcopy(DEFAULT_CONFIG))
        for key, value in DEFAULT_CONFIG.items():
            self.config.setdefault(key, copy.deepcopy(value))
        self.workflows = self.read('workflows', {})
        self.jobs = self.read('jobs', {})
        self.characters = self.read('characters', {})
        self.panels = self.read('panels', {})
        self.prompt_presets = self.read('prompt_presets', {})
        self.comic_review = self.read('comic_review', {'revision': 0, 'assets': {}, 'events': []})

    def read(self, name, default):
        path = self.directory / f'{name}.json'
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f'数据文件损坏，请保留并检查 {path}') from exc

    def save(self, name):
        path = self.directory / f'{name}.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(getattr(self, name), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(path)

    def public_config(self):
        return {**{key: value for key, value in self.config.items() if key not in {'api_key','novelai_token'}},
                'has_api_key': bool(self.config['api_key']), 'has_novelai_token':bool(self.config['novelai_token'])}
