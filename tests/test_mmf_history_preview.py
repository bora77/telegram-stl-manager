import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('mmf_history_preview', Path(__file__).resolve().parents[1] / 'tools/preview-mmf-history.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class HistoryPreviewTests(unittest.TestCase):
    def test_receipts_do_not_hide_updates_or_filename_changes(self):
        item = {'key': 'current', 'filename': 'model.zip', 'updated_at': '2026-01-01T12:00:00+00:00'}
        receipt = {'filename': 'artist^^model.zip', 'reported_downloaded_at': '2026-01-02T12:00:00+01:00'}
        self.assertTrue(preview.receipt_status(item, receipt, set()).startswith('Candidate:'))
        item['updated_at'] = '2026-01-03T12:00:00+00:00'
        self.assertEqual(preview.receipt_status(item, receipt, set()), 'Updated after legacy download')
        item['updated_at'] = None
        self.assertEqual(preview.receipt_status(item, receipt, set()), 'Review: revision date unavailable')
        item['filename'] = 'new.zip'
        self.assertEqual(preview.receipt_status(item, receipt, set()), 'Review: filename differs')
        self.assertEqual(preview.receipt_status(item, None, set()), 'No legacy receipt')

    def test_monthly_rewards_match_but_named_rewards_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            artist = base / '- Example'
            month = artist / 'Example 2026-09'
            month.mkdir(parents=True)
            (month / 'repacked.7z').write_bytes(b'test')
            item = {'folder': '- Example', 'release': 'September 2026 Standard Rewards',
                    'release_folder': 'Example September 2026 Standard Rewards', 'release_month': None}
            match, path, files = preview.folder_match(item, base)
            self.assertEqual(match, 'Artist/month match')
            self.assertEqual(Path(path), month)
            self.assertEqual(files, ['repacked.7z'])
            item.update(release='1 Year Rewards', release_folder='Example 1 Year Rewards', release_month='2026-09')
            self.assertEqual(preview.folder_match(item, base)[0], 'Review: named collection')
            item.update(release='September 2026 Standard Rewards', release_folder='Example September 2026 Standard Rewards')
            (artist / '2026-09').mkdir()
            self.assertEqual(preview.folder_match(item, base)[0], 'Review: multiple folder matches')


if __name__ == '__main__':
    unittest.main()
