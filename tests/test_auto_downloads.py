import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from app.auto_downloads import AutoDownloads
from app.subscription_store import SubscriptionStore


class AutoDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=SubscriptionStore(self.root)
        self.store.atomic_write(self.store.config_path,{'revision':1,'download_directory':str(self.root),'mmf_beta_enabled':True})
        self.store.read=Mock(return_value={'revision':2})
        self.store.runs=Mock();self.store.runs.status.return_value={'state':'completed'}
        self.available=Mock();self.available.status.return_value={'checked_at':123,'files':2,'checking':False,'errors':[]}
        self.values={'checked_at':456,'download_checked_at':0,'items':[{'key':'new'}],'queue':[],'job':{'action':'check','phase':'complete'}}
        self.mmf=Mock();self.mmf.session=self.root/'session';self.mmf.session.touch()
        self.mmf.get.side_effect=lambda k,d=None:self.values.get(k,d)
        self.mmf.busy.return_value=False;self.mmf.completed.return_value=set();self.mmf.pending_redownload_keys.return_value=set()
        self.mmf.scoped_items.side_effect=lambda items:items
        self.auto=AutoDownloads(self.store,self.available,self.mmf)

    def test_defaults_off_and_flags_persist_independently(self):
        self.auto.tick();self.store.runs.start.assert_not_called();self.mmf.start.assert_not_called()
        self.auto.save({'provider':'telegram','enabled':True})
        saved=AutoDownloads(self.store,self.available,self.mmf).read()
        self.assertTrue(saved['telegram']['enabled']);self.assertFalse(saved['mmf']['enabled'])
        for payload in ({'provider':'gumroad','enabled':True},{'provider':'mmf','enabled':'yes'}):
            with self.assertRaises(ValueError):self.auto.save(payload)

    def test_two_clients_start_each_check_only_once(self):
        self.auto.save({'provider':'telegram','enabled':True});self.auto.save({'provider':'mmf','enabled':True})
        self.auto.tick();AutoDownloads(self.store,self.available,self.mmf).tick()
        self.store.runs.start.assert_called_once_with({'revision':2,'config_revision':1,'mode':'detected'})
        self.mmf.start.assert_called_once_with('download')
        self.available.status.return_value['checked_at']=124;self.values['checked_at']=457
        self.auto.tick();self.assertEqual(self.store.runs.start.call_count,2);self.assertEqual(self.mmf.start.call_count,2)

    def test_simultaneous_tabs_do_not_duplicate_runs(self):
        from concurrent.futures import ThreadPoolExecutor
        self.auto.save({'provider':'telegram','enabled':True})
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda n:AutoDownloads(self.store,self.available,self.mmf).tick(),range(4)))
        self.store.runs.start.assert_called_once()
        self.assertTrue(all(r['telegram']['last_check']==123 for r in results))

    def test_empty_incomplete_or_claimed_checks_do_not_start(self):
        self.auto.save({'provider':'telegram','enabled':True})
        for change in ({'files':0},{'checking':True},{'errors':['Incomplete check']},{'claimed':True},{'checked_at':None}):
            self.available.status.return_value={'checked_at':123,'files':2,'errors':[],**change}
            self.auto.tick()
        self.store.runs.start.assert_not_called()

    def test_active_or_unfinished_runs_are_not_restarted(self):
        self.auto.save({'provider':'telegram','enabled':True});self.auto.save({'provider':'mmf','enabled':True})
        for state in ('downloading','stopped','failed','interrupted','needs_review'):
            self.store.runs.status.return_value={'state':state}
            self.values['queue']=[{'key':'unfinished'}];self.auto.tick()
        self.store.runs.start.assert_not_called();self.mmf.start.assert_not_called()

    def test_failed_start_is_not_repeated_and_off_does_not_stop_worker(self):
        self.auto.save({'provider':'telegram','enabled':True})
        self.store.runs.start.side_effect=ValueError('Test failure')
        self.auto.tick();self.auto.tick();self.store.runs.start.assert_called_once()
        self.assertIn('Test failure',self.auto.read()['telegram']['message'])
        self.auto.save({'provider':'telegram','enabled':False});self.auto.tick()
        self.store.runs.stop.assert_not_called()

    def test_busy_race_is_deferred_not_claimed(self):
        self.auto.save({'provider':'telegram','enabled':True})
        self.store.runs.start.side_effect=FileExistsError('busy');self.auto.tick()
        self.assertIsNone(self.auto.read()['telegram']['last_check'])
        self.store.runs.start.side_effect=None;self.auto.tick()
        self.assertEqual(self.store.runs.start.call_count,2)

    def test_unavailable_storage_and_disabled_mmf_do_not_start(self):
        self.auto.save({'provider':'telegram','enabled':True});self.auto.save({'provider':'mmf','enabled':True})
        self.store.atomic_write(self.store.config_path,{'revision':1,'download_directory':str(self.root/'missing'),'mmf_beta_enabled':False})
        self.auto.tick();self.store.runs.start.assert_not_called();self.mmf.start.assert_not_called()
        self.assertIsNone(self.auto.read()['telegram']['last_check'])
