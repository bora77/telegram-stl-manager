import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock
from app.mmf_manager import MMFManager, file_in_scope
from app.mmf_reconcile import match


class ReconcileTests(unittest.TestCase):
    def test_file_update_cutoff_does_not_use_collection_name(self):
        item={'release':'January 2024 Standard Rewards','updated_at':'2026-09-01T00:00:00Z'}
        self.assertTrue(file_in_scope(item,'2026-09'))
        self.assertFalse(file_in_scope({**item,'updated_at':'2026-08-31T23:59:59Z'},'2026-09'))
        self.assertFalse(file_in_scope({**item,'release':'September 2026','updated_at':'2024-01-01T00:00:00Z'},'2026-09'))
        for date in (None,'','v1','2026-09-01'):
            self.assertFalse(file_in_scope({**item,'updated_at':date},'2026-09'))
            self.assertTrue(file_in_scope({**item,'updated_at':date},''))

    def test_month_name_size_and_collection_must_match(self):
        item = {'filename': 'UsefulKitbashItems.zip', 'size': 123,
                'release': 'August 2026 Standard Rewards'}
        entry = {'filename': item['filename'], 'size': 123, 'context': '3DArtGuy 2026-08'}
        self.assertEqual(match(item, [entry]), entry)
        for changed in ({'size': 124}, {'context': '3DArtGuy 2026-07'},
                        {'context': '3DArtGuy Loyalty 1 year 2026-08'}, {'filename': 'other.zip'}):
            self.assertIsNone(match(item, [{**entry, **changed}]))
        item['release'] = '3DArtGuy 1 Year Rewards'
        self.assertIsNone(match(item, [entry]))
        loyalty = {**entry, 'context': '3DArtGuy - Loyalty 1 year rewards set three 2026-08'}
        self.assertEqual(match(item, [loyalty]), loyalty)
        self.assertIsNone(match(item, [{**loyalty, 'context': '3DArtGuy 3 Month Rewards'}]))

    def test_download_only_transfers_unrecorded_independent_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = MMFManager.__new__(MMFManager)
            manager.directory = Path(tmp)
            common = dict(creator_id='artist', creator='Artist', folder='Artist',
                          release='August 2026', object_id=1, size=123, updated_at='today')
            items = [{**common, 'key': key, 'archive_id': n, 'filename': key+'.zip'}
                     for n, key in enumerate(('existing', 'new'), 1)]
            state = {'queue': items, 'run_base': tmp}
            manager.get = lambda key, default=None: state.get(key, default)
            manager.completed = lambda: {'existing'}
            manager.pending_redownload_keys = lambda: set()
            manager.stopped = lambda: False
            manager.progress = Mock()
            manager.deliver_originals = Mock()
            client = Mock()
            client.downloadables.return_value = {'archives': [
                {'id': i['archive_id'], 'size': 123, 'updatedAt': 'today'} for i in items]}
            manager.client = lambda: client
            manager.download()
            self.assertEqual(client.download.call_count, 1)
            self.assertEqual(client.download.call_args.args[0]['key'], 'new')
            self.assertEqual([i['key'] for i in manager.deliver_originals.call_args.args[0]], ['new'])
