"""PNG's semi-automatic selection and WAI region-redraw, without canvas/UI nodes."""
import base64
import io
import json
import math
import uuid
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
import cv2
import numpy as np

WAI = 'waiIllustriousSDXL_v170.safetensors'
SAM = 'sam3.1_multiplex_fp16.safetensors'
MAX_BYTES = 20 * 1024 * 1024


def decode_image(content):
    if not content or len(content) > MAX_BYTES:
        raise ValueError('图片须小于 20 MB。')
    try:
        with Image.open(io.BytesIO(content)) as raw:
            if raw.width > 4096 or raw.height > 4096 or raw.width * raw.height > 16_777_216:
                raise ValueError('图片边长须不超过 4096 像素，请先缩小图片。')
            image = ImageOps.exif_transpose(raw).convert('RGB')
            image.load()
        out = io.BytesIO(); image.save(out, format='PNG')
        return out.getvalue(), image.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError('文件不是可读取的图片，请使用 PNG、JPEG 或 WebP。') from None


class EditAssets:
    def __init__(self, directory):
        self.root = Path(directory) / 'edits'
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, key):
        try:
            if str(uuid.UUID(key)) != key: raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('无效的改图记录。') from None
        return self.root / key

    def create(self, content):
        content, size = decode_image(content)
        key = str(uuid.uuid4()); folder = self.folder(key); folder.mkdir()
        (folder / 'original.png').write_bytes(content)
        edit = {'id': key, 'width': size[0], 'height': size[1], 'selection_job_id': None, 'render_job_id': None}
        self.save(edit)
        return self.public(edit)

    def get(self, key):
        path = self.folder(key) / 'session.json'
        if not path.is_file(): raise ValueError('改图记录不存在，请重新上传图片。')
        return json.loads(path.read_text(encoding='utf-8'))

    def save(self, edit):
        path = self.folder(edit['id']) / 'session.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(edit, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)

    def original(self, key):
        self.get(key)
        return (self.folder(key) / 'original.png').read_bytes()

    @staticmethod
    def public(edit):
        return {**edit, 'image_url': '/api/edit/' + edit['id'] + '/original'}


def number(data, name, default, low, high, integer=False):
    raw = data.get(name, default)
    try:
        result = float(raw)
        if isinstance(raw, bool) or not math.isfinite(result) or not low <= result <= high or (integer and result != int(result)):
            raise ValueError()
        return int(result) if integer else result
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f'{name} 参数须在 {low}–{high} 之间。') from None


def points(data, field, size):
    coords = data.get(field, [])
    if not isinstance(coords, list) or len(coords) > 40: raise ValueError('每类点选最多 40 个。')
    result = []
    for p in coords:
        if not isinstance(p, dict): raise ValueError('点选坐标格式不正确。')
        result.append({'x': number(p, 'x', -1, 0, size[0]-1, True), 'y': number(p, 'y', -1, 0, size[1]-1, True)})
    return json.dumps(result)


def node(kind, **inputs):
    return {'class_type': kind, 'inputs': inputs}


def selection_graph(image, data, size):
    region = data.get('region', '').strip()
    if len(region) > 200: raise ValueError('部位描述须不超过 200 字符。')
    # Native SAM defaults to one detection per category, which makes choosing
    # between two characters impossible. Request candidates before point filtering.
    categories = [part.strip() for part in region.split(',') if part.strip()]
    candidate_text = ', '.join(part + ':10' if ':' not in part else part for part in categories)
    positive = points(data, 'positive_coords', size)
    negative = points(data, 'negative_coords', size)
    if not region and positive == '[]': raise ValueError('请描述修改部位，或点选需要包含的位置。')
    graph = {
        '1': node('LoadImage', image=image),
        '2': node('CheckpointLoaderSimple', ckpt_name=SAM),
        '3': node('CLIPTextEncode', clip=['2', 1], text=candidate_text),
        '4': node('SAM3_Detect', model=['2', 0], image=['1', 0], threshold=number(data, 'threshold', .5, .05, .99),
                  refine_iterations=2, individual_masks=False),
        '5': node('MaskToImage', mask=['4', 0]),
        '6': node('SaveImage', images=['5', 0], filename_prefix='LocalImageStudio/Edit/Masks'),
    }
    if region: graph['4']['inputs']['conditioning'] = ['3', 0]
    else:
        graph.pop('3')
        graph['4']['inputs'].update(positive_coords=positive, negative_coords=negative)
    return graph


def redraw_graph(image, mask, data):
    return {
        '1': node('LoadImage', image=image),
        '2': node('LoadImageMask', image=mask, channel='red'),
        '3': node('INPAINT_ExpandMask', mask=['2', 0], grow=number(data, 'grow', 4, 0, 64, True), blur=number(data, 'blur', 3, 0, 32, True), blur_type='gaussian'),
        '4': node('MaskToSEGS', mask=['3', 0], combined=False, crop_factor=3.0, bbox_fill=False, drop_size=8, contour_fill=True),
        '5': node('CheckpointLoaderSimple', ckpt_name=WAI),
        '6': node('CLIPSetLastLayer', clip=['5', 1], stop_at_clip_layer=-2),
        '7': node('CLIPTextEncode', clip=['6', 0], text='%prompt%'),
        '8': node('CLIPTextEncode', clip=['6', 0], text='%negative_prompt%'),
        '9': node('DetailerForEach', image=['1', 0], segs=['4', 0], model=['5', 0], clip=['6', 0], vae=['5', 2],
                  positive=['7', 0], negative=['8', 0], guide_size=512.0, guide_size_for=True, max_size=1024.0,
                  seed='%seed%', steps=number(data, 'steps', 20, 1, 60, True), cfg=number(data, 'cfg', 6, 1, 15),
                  sampler_name='euler_ancestral', scheduler='normal', denoise=number(data, 'denoise', .5, .05, 1),
                  feather=5, noise_mask=True, force_inpaint=True, wildcard='', cycle=1, inpaint_model=False, noise_mask_feather=10),
        '10': node('SaveImage', images=['9', 0], filename_prefix='LocalImageStudio/Edit/WAI'),
    }


def check_mask(content, size):
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.size != size: raise ValueError('选区与原图尺寸不一致，请重新定位。')
            gray = image.convert('L')
            if gray.getextrema()[1] < 128: raise ValueError('没有识别到可修改区域，请调整部位描述、点选或识别阈值。')
            out = io.BytesIO(); gray.save(out, format='PNG')
            return out.getvalue()
    except (UnidentifiedImageError, OSError):
        raise ValueError('选区图片无法读取，请重新定位。') from None


def filter_mask(content, size, selection):
    content = check_mask(content, size)
    # SAM's text and point paths are unioned internally. For a text selection,
    # apply points to connected candidate regions rather than unioning tracker masks.
    if not selection.get('region', '').strip(): return content
    with Image.open(io.BytesIO(content)) as image:
        binary = (np.asarray(image.convert('L')) >= 128).astype(np.uint8)
    count, labels = cv2.connectedComponents(binary, connectivity=8)
    positive = {int(labels[p['y'], p['x']]) for p in selection.get('positive_coords', [])} - {0}
    negative = {int(labels[p['y'], p['x']]) for p in selection.get('negative_coords', [])} - {0}
    if selection.get('positive_coords') and not positive:
        raise ValueError('包含点没有落在识别选区内，请调整点选位置或部位描述。')
    chosen = (positive if selection.get('positive_coords') else set(range(1, count))) - negative
    final = np.isin(labels, list(chosen)).astype(np.uint8) * 255
    out = io.BytesIO(); Image.fromarray(final).save(out, format='PNG')
    return check_mask(out.getvalue(), size)


def prompt_image(content):
    # Bound vision payload while retaining source image separately at full resolution.
    with Image.open(io.BytesIO(content)) as image:
        image.thumbnail((1536, 1536))
        out = io.BytesIO(); image.save(out, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode('ascii')
