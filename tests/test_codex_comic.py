import tempfile
import unittest
from pathlib import Path
from codex_comic import check_retry, save


class CodexComicTests(unittest.TestCase):
    def test_no_auto_retry_and_human_revision_needs_no_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            check_retry([])
            save(p / 'record.json', {'state': 'done', 'confirmed': False})
            with self.assertRaises(ValueError):
                check_retry([p])
            check_retry([p], 'Change the pose')
            save(p / 'record.json', {'state': 'unknown', 'confirmed': False})
            with self.assertRaises(ValueError):
                check_retry([p], 'Change the pose')

    def test_confirmed_result_is_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            save(p / 'record.json', {'state': 'done', 'confirmed': True})
            with self.assertRaises(ValueError):
                check_retry([p], 'Change the pose')
