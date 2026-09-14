from test_support import configure_source
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from subscription_store import SubscriptionStore
from download_history import DownloadHistory

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = SubscriptionStore(configure_source(self.root))
        self.url = 'https://t.me/c/123456789/123'
        (self.root/'data/creators.json').write_text(json.dumps({'creators':[{'name':'Example','topic_url':self.url,'within_approved_group':True}]}))
        self.entry = dict(topic_url=self.url,creator_folder='- Example',layout='monthly',month_basis='release',download_scope='from_month',start_month='2026-08')
    def save(self, **changes):
        return self.store.save(dict(revision=0,config_revision=0,subscriptions=[dict(self.entry,**changes)]))
    def test_save_survives_reopen_and_rejects_stale_updates(self):
        saved = self.save()
        self.assertEqual(SubscriptionStore(configure_source(self.root)).read(),saved)
        with self.assertRaises(FileExistsError): self.save()
        self.assertEqual(self.store.read(),saved)
    def test_invalid_destinations_scopes_and_groups(self):
        for changes in [dict(creator_folder='../escape'),dict(download_scope=''),dict(download_scope='future'),dict(topic_url='https://t.me/c/9/123'),dict(layout='flat')]:
            with self.subTest(changes=changes),self.assertRaises(ValueError): self.save(**changes)
        self.assertEqual(self.store.read()['revision'],0)
    def test_config_change_invalidates_old_subscription_form(self):
        self.store.save_config(dict(revision=0,download_directory=str(self.root)))
        with self.assertRaises(FileExistsError):self.save()
        self.assertFalse((self.root/'- Example').exists())

    def test_server_comparison_frequency_is_optional_saved_and_validated(self):
        self.assertEqual(self.store.config()['server_check_frequency'],'cached')
        saved=self.store.save_config(dict(revision=0,download_directory=str(self.root),server_check_frequency='release'))
        self.assertEqual(self.store.config()['server_check_frequency'],'release')
        # A client opened before this setting existed must not erase it.
        self.store.save_config(dict(revision=1,download_directory=str(self.root)))
        self.assertEqual(self.store.config()['server_check_frequency'],'release')
        with self.assertRaises(ValueError):self.store.save_config(dict(revision=2,download_directory=str(self.root),server_check_frequency='file'))
        self.assertEqual(self.store.config()['revision'],2)

    def test_speed_threshold_is_saved_and_older_forms_preserve_it(self):
        self.assertEqual(self.store.config()['server_speed_threshold_mbps'],0)
        saved=self.store.save_config(dict(revision=0,download_directory=str(self.root),server_speed_threshold_mbps=20))
        self.assertEqual(saved['server_speed_threshold_mbps'],20)
        self.store.save_config(dict(revision=1,download_directory=str(self.root)))
        self.assertEqual(self.store.config()['server_speed_threshold_mbps'],20)
        before=self.store.config_path.read_bytes()
        for value in (-1,1001,10**1000,True,False,None,'20',float('nan'),float('inf'),[],{}):
            with self.subTest(value=repr(value)),self.assertRaises(ValueError):
                self.store.save_config(dict(revision=2,download_directory=str(self.root),server_speed_threshold_mbps=value))
            self.assertEqual(self.store.config_path.read_bytes(),before)
        for revision,value in [(2,25.5),(3,0)]:
            saved=self.store.save_config(dict(revision=revision,download_directory=str(self.root),server_speed_threshold_mbps=value))
            self.assertEqual(saved['server_speed_threshold_mbps'],value)
    def test_bandwidth_target_roundtrip_validation_and_older_clients(self):
        self.assertEqual(self.store.config()['download_bandwidth_target_mbps'],30)
        self.store.save_config(dict(revision=0,download_directory=str(self.root),download_bandwidth_target_mbps=45.5))
        self.assertEqual(SubscriptionStore(self.root).config()['download_bandwidth_target_mbps'],45.5)
        self.store.save_config(dict(revision=1,download_directory=str(self.root)))
        self.assertEqual(self.store.config()['download_bandwidth_target_mbps'],45.5)
        before=self.store.config_path.read_bytes()
        for value in (0,-1,1001,10**1000,True,None,'30',float('nan'),float('inf')):
            with self.subTest(value=repr(value)),self.assertRaises(ValueError):
                self.store.save_config(dict(revision=2,download_directory=str(self.root),download_bandwidth_target_mbps=value))
            self.assertEqual(self.store.config_path.read_bytes(),before)

    def test_custom_month_survives_save_and_invalid_month_is_rejected(self):
        saved=self.save(download_scope='from_month',start_month='2025-03')
        self.assertEqual(saved['subscriptions'][0]['start_month'],'2025-03')
        self.assertEqual(SubscriptionStore(configure_source(self.root)).read()['subscriptions'][0]['download_scope'],'from_month')
        for value in ('',None,'2025-13','2025-3'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.save(download_scope='from_month',start_month=value)
    def test_history_survives_file_removal_and_duplicate_discovery(self):
        history=self.store.history
        item=history.register(source_message_id=5,creator='Example',topic_url=self.url,filename='test.zip',bytes_total=4)
        history.set_progress(item['id'],2)
        self.assertEqual(history.queue()['bytes_downloaded'],2)
        with self.assertRaises(ValueError): history.complete(item['id'],destination=str(self.root/'test.zip'),verified_size=2,sha256='0'*64)
        path=self.root/'test.zip';path.write_bytes(b'test')
        history.complete(item['id'],destination=str(path),verified_size=4,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        path.unlink()
        reopened=DownloadHistory(history.path)
        self.assertTrue(reopened.was_downloaded(5))
        duplicate=reopened.register(source_message_id=5,creator='Example',topic_url=self.url,filename='test.zip')
        self.assertEqual(duplicate['state'],'downloaded')
        with self.assertRaises(ValueError):reopened.set_progress(item['id'],0)
        reopened.register(source_message_id=6,creator='Example',topic_url=self.url,filename='test.zip')
        self.assertFalse(reopened.was_downloaded(6))
        self.assertEqual(reopened.queue()['history_completed'],1)

    def test_legacy_future_scope_becomes_explicit_month_without_changing_its_baseline(self):
        for month in ('2025-03',None):
            with self.subTest(month=month):
                entry={**self.entry,'download_scope':'future','subscribed_at':'2026-01-12T12:00:00+00:00'}
                if month is None:entry.pop('start_month')
                else:entry['start_month']=month
                data={'version':2,'revision':0,'saved_at':entry['subscribed_at'],'subscriptions':[entry]}
                self.store.atomic_write(self.store.path,data);before=self.store.path.read_bytes()
                converted=self.store.read()['subscriptions'][0]
                self.assertEqual(converted['download_scope'],'from_month')
                self.assertEqual(converted['start_month'],month or '2025-12')
                self.assertEqual(self.store.path.read_bytes(),before)
                saved=self.store.save({'revision':0,'config_revision':0,'subscriptions':[converted]})
                self.assertEqual(saved['subscriptions'][0]['start_month'],month or '2025-12')
                self.assertEqual(saved['subscriptions'][0]['download_scope'],'from_month')

if __name__=='__main__':unittest.main()
