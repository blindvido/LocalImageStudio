"""ComfyUI API graphs and Zhihuiji variable conventions."""
import copy
import math
import re

VARIABLE = re.compile(r'%([A-Za-z_][A-Za-z_0-9]*)%')
NUMERIC = {'width', 'height', 'seed', 'steps', 'cfg_scale'}


def inspect_graph(graph):
    if not isinstance(graph, dict) or not graph or any(
        not isinstance(node, dict) or not isinstance(node.get('class_type'), str)
        or not isinstance(node.get('inputs'), dict) for node in graph.values()
    ):
        raise ValueError('请导入 ComfyUI API 格式 JSON（节点须包含 class_type 和 inputs），普通画布 JSON 不支持。')
    variables, fields = set(), []

    def scan(value):
        if isinstance(value, str):
            variables.update(VARIABLE.findall(value))
        elif isinstance(value, dict):
            for child in value.values():
                scan(child)
        elif isinstance(value, list):
            for child in value:
                scan(child)

    for node_id, node in graph.items():
        scan(node['inputs'])
        for field, value in node['inputs'].items():
            if not isinstance(value, (list, dict)):
                fields.append({'node': node_id, 'field': field, 'value': value,
                               'title': node.get('_meta', {}).get('title', node['class_type']),
                               'class_type': node['class_type']})
    return {'variables': sorted(variables), 'fields': fields}


def parameter_value(name, value):
    if name not in NUMERIC:
        return value
    try:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError()
        if name != 'cfg_scale' and (number != int(number) or isinstance(value, bool)):
            raise ValueError()
        if name in {'width', 'height', 'steps'} and number <= 0:
            raise ValueError()
        if name in {'seed', 'cfg_scale'} and number < 0:
            raise ValueError()
        if name == 'seed' and number > 2**53 - 1:
            raise ValueError()
        return number if name == 'cfg_scale' else int(number)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f'参数 {name} 不是有效数值。') from None


def render_graph(graph, values, mappings):
    info = inspect_graph(graph)
    missing = [name for name in info['variables'] if name not in values or values[name] is None
               or (values[name] == '' and name not in {'prompt', 'negative_prompt'})]
    if missing:
        raise ValueError('未配置工作流变量：' + ', '.join(missing))
    values = {name: parameter_value(name, value) for name, value in values.items()}

    def replace(value):
        if isinstance(value, str):
            match = VARIABLE.fullmatch(value)
            if match:
                return values[match[1]]
            return VARIABLE.sub(lambda m: str(values[m[1]]), value)
        if isinstance(value, list):
            return [replace(child) for child in value]
        if isinstance(value, dict):
            return {key: replace(child) for key, child in value.items()}
        return value

    result = copy.deepcopy(graph)
    for node in result.values():
        node['inputs'] = replace(node['inputs'])
    for name, target in mappings.items():
        if not isinstance(target, list) or len(target) != 2:
            raise ValueError(f'无效参数映射：{name}')
        node_id, field = target
        if node_id not in result or field not in result[node_id]['inputs'] or isinstance(result[node_id]['inputs'][field], (list, dict)):
            raise ValueError(f'参数映射不存在或是节点连接：{name}')
        if name not in values:
            raise ValueError(f'映射参数未配置：{name}')
        result[node_id]['inputs'][field] = values[name]
    return result
