import copy
import unittest

from workflow import inspect_graph, render_graph


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.graph = {
            '1': {'class_type': 'CLIPTextEncode', 'inputs': {'text': '%prompt%'}},
            '2': {'class_type': 'KSampler', 'inputs': {'seed': '%seed%', 'steps': '%steps%', 'cfg': '%cfg_scale%', 'positive': ['1', 0]}},
        }

    def test_types_escaping_and_original_preserved(self):
        original = copy.deepcopy(self.graph)
        rendered = render_graph(self.graph, {'prompt': '窗前的女孩 "hello"\n雨夜', 'seed': '42', 'steps': 20, 'cfg_scale': '5.5'}, {})
        self.assertEqual(rendered['1']['inputs']['text'], '窗前的女孩 "hello"\n雨夜')
        self.assertEqual(rendered['2']['inputs']['seed'], 42)
        self.assertEqual(rendered['2']['inputs']['cfg'], 5.5)
        self.assertEqual(rendered['2']['inputs']['positive'], ['1', 0])
        self.assertEqual(self.graph, original)

    def test_unknown_variable_blocks_submission(self):
        self.graph['1']['inputs']['text'] = '%custom%'
        with self.assertRaisesRegex(ValueError, 'custom'):
            render_graph(self.graph, {'seed': 1, 'steps': 20, 'cfg_scale': 7}, {})

    def test_manual_mapping_and_embedded_prompt(self):
        graph = {'1': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'quality, %prompt%'}}}
        self.assertEqual(render_graph(graph, {'prompt': 'cat'}, {})['1']['inputs']['text'], 'quality, cat')
        graph['1']['inputs']['text'] = 'old'
        self.assertEqual(render_graph(graph, {'prompt': 'dog'}, {'prompt': ['1', 'text']})['1']['inputs']['text'], 'dog')

    def test_invalid_mapping_rejected(self):
        with self.assertRaisesRegex(ValueError, '映射'):
            render_graph(self.graph, {'prompt': 'cat', 'seed': 1, 'steps': 20, 'cfg_scale': 7}, {'prompt': ['99', 'text']})

    def test_negative_prompt_can_be_empty(self):
        graph = {'1': {'class_type': 'CLIPTextEncode', 'inputs': {'text': '%negative_prompt%'}}}
        self.assertEqual(render_graph(graph, {'negative_prompt': ''}, {})['1']['inputs']['text'], '')

    def test_canvas_json_rejected(self):
        with self.assertRaisesRegex(ValueError, 'API'):
            inspect_graph({'nodes': [], 'links': []})

    def test_fields_and_variables(self):
        info = inspect_graph(self.graph)
        self.assertEqual(info['variables'], ['cfg_scale', 'prompt', 'seed', 'steps'])
        self.assertTrue(any(f['node'] == '1' and f['field'] == 'text' for f in info['fields']))

    def test_invalid_numeric_parameter_rejected(self):
        for value in ['nan', -1, 0, 1.5]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                render_graph(self.graph, {'prompt': 'cat', 'seed': 1, 'steps': value, 'cfg_scale': 7}, {})

    def test_custom_variable_numeric_and_nonrecursive_text(self):
        graph = {'1': {'class_type': 'X', 'inputs': {'amount': '%custom%', 'text': '%prompt%'}}}
        result = render_graph(graph, {'custom': 0.5, 'prompt': 'shirt with %unknown% logo'}, {})
        self.assertEqual(result['1']['inputs']['amount'], 0.5)
        self.assertEqual(result['1']['inputs']['text'], 'shirt with %unknown% logo')


if __name__ == '__main__':
    unittest.main()
