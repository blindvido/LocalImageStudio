import copy
import unittest
import novelai
from nai_panels import normalize_nai_prompt


def prompt_data():
    return {'base_positive': '1girl, 1boy, ruins, anime coloring', 'base_negative': 'text',
            'characters': [
                {'character_id': 'belle', 'positive': 'girl, blue hair', 'negative': 'silver hair', 'center': {'x': .3, 'y': .5}},
                {'character_id': 'wise', 'positive': 'boy, silver hair', 'negative': 'blue hair', 'center': {'x': .7, 'y': .5}}]}


class CharacterPayloadTests(unittest.TestCase):
    def test_five_character_limit_and_captions(self):
        chars = [{'character_id':str(i),'positive':f'person {i}','negative':f'avoid {i}','direction':f'action {i}'} for i in range(5)]
        for model in ('nai-diffusion-5-full','nai-diffusion-4-5-full'):
            p = novelai.payload('5 people', '', {'MODEL_NAME':model}, characters=chars)['parameters']
            self.assertEqual(len(p['v4_prompt']['caption']['char_captions']),5)
            self.assertEqual(p['v4_prompt']['caption']['char_captions'][4]['char_caption'],'person 4, action 4')
            self.assertEqual(p['v4_negative_prompt']['caption']['char_captions'][4]['char_caption'],'avoid 4')
        with self.assertRaises(ValueError):
            normalize_nai_prompt({'characters':chars+[{'character_id':'six','positive':'person'}]})

    def test_two_characters_positive_negative_and_coords(self):
        data = prompt_data()
        body = novelai.payload(data['base_positive'], data['base_negative'], {'MODEL_NAME': 'nai-diffusion-4-5-full'}, characters=data['characters'])
        p = body['parameters']
        self.assertTrue(p['v4_prompt']['use_coords'])
        self.assertEqual(p['v4_prompt']['caption']['char_captions'][0], {'char_caption': 'girl, blue hair', 'centers': [{'x': .3, 'y': .5}]})
        self.assertEqual(p['v4_negative_prompt']['caption']['char_captions'][1]['char_caption'], 'blue hair')
        self.assertEqual(p['v4_negative_prompt']['caption']['char_captions'][1]['centers'], [{'x': .7, 'y': .5}])

    def test_invalid_characters_rejected_before_submit(self):
        chars = prompt_data()['characters']
        for bad in [chars + [chars[0]], [{**chars[0], 'center': {'x': float('nan'), 'y': .5}}], [{**chars[0], 'center': {'x': 1.1, 'y': .5}}]]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                novelai.payload('two people', '', {}, characters=bad)
        with self.assertRaises(ValueError):
            novelai.payload('two people', '', {'MODEL_NAME': 'nai-diffusion-3'}, characters=chars)

    def test_grid_v45_and_free_position_v5(self):
        chars = copy.deepcopy(prompt_data()['characters']); chars[0]['center']['x'] = .24
        for model, expected in [('nai-diffusion-4-5-full', .3), ('nai-diffusion-5-full', .24)]:
            p = novelai.payload('two people', '', {'MODEL_NAME': model}, characters=chars)['parameters']
            self.assertEqual(p['v4_prompt']['caption']['char_captions'][0]['centers'][0]['x'], expected)

    def test_old_request_and_zero_one_characters(self):
        old = novelai.payload('flower', '', {})
        self.assertEqual(old, novelai.payload('flower', '', {}, characters=[]))
        self.assertEqual(len(novelai.payload('girl', '', {}, characters=prompt_data()['characters'][:1])['parameters']['v4_prompt']['caption']['char_captions']), 1)

    def test_character_anchor_stays_separate_from_per_panel_direction_until_payload(self):
        anchor = 'girl, short dark blue hair, green eyes, orange hairclip, black and orange hooded vest'
        direction = 'close-up, turning her head toward screen right, slightly raised eyebrow, teasing expression'
        prompt = normalize_nai_prompt({'base_positive': 'color comic, quiet Hollow walkway', 'characters': [
            {'character_id': 'belle', 'positive': anchor, 'direction': direction, 'center': {'x': .5, 'y': .5}}
        ]})
        self.assertEqual(prompt['characters'][0]['positive'], anchor)
        self.assertEqual(prompt['characters'][0]['direction'], direction)
        body = novelai.payload(prompt['base_positive'], '', {}, characters=prompt['characters'])
        self.assertEqual(body['parameters']['v4_prompt']['caption']['char_captions'][0]['char_caption'], anchor + ', ' + direction)

    def test_references_are_not_silently_ignored(self):
        with self.assertRaises(ValueError):
            novelai.payload('girl', '', {'reference_image': 'base64'})

    def test_split_syntax_not_mixed_with_slots(self):
        with self.assertRaises(ValueError):
            novelai.payload('2people | girl', '', {}, characters=prompt_data()['characters'])
