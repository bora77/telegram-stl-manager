from tests.test_support import configure_source
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from app.subscription_store import SubscriptionStore
from app.download_history import DownloadHistory

class StoreTests(unittest.TestCase):
    def test_separate_mmf_check_interval(self):
        current=self.store.config()
        result=self.store.save_config({'revision':current['revision'],'download_directory':str(self.root),'availability_interval_hours':8,'mmf_availability_interval_hours':2})
        self.assertEqual(result['availability_interval_hours'],8)
        self.assertEqual(self.store.config()['mmf_availability_interval_hours'],2)
        for value in (0,169,True,'4',float('nan')):
            with self.assertRaises(ValueError):self.store.save_config({'revision':result['revision'],'download_directory':str(self.root),'mmf_availability_interval_hours':value})

    def test_release_packaging_settings(self):
        config=self.store.config()
        self.assertEqual((config['release_compression_level'],config['release_volume_mib']),(7,4000))
        payload={'revision':config['revision'],'download_directory':str(self.root),'release_compression_level':3,'release_volume_mib':1500}
        result=self.store.save_config(payload)
        self.assertEqual((result['release_compression_level'],result['release_volume_mib']),(3,1500))
        for key,values in {'release_compression_level':[True,2,10,'7'],'release_volume_mib':[True,0,1.5,65537]}.items():
            for value in values:
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                    self.store.save_config({**payload,'revision':result['revision'],key:value})
        result=self.store.save_config({'revision':result['revision'],'download_directory':str(self.root)})
        self.assertEqual((result['release_compression_level'],result['release_volume_mib']),(3,1500))

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
    def test_task_status_contains_only_completion_metadata_and_does_not_write(self):
        self.assertTrue(all(task['state']=='idle' for task in self.store.task_status()['tasks'].values()))
        records={
            'run.json':{'id':'run','state':'needs_review','started_at':'start','finished_at':'finish','subscriptions':[{'private':'not exposed'}]},
            'organizer-plan.json':{'id':'preview','state':'completed','creator':'Example','files':[{'source':'private path'}],'topic_url':self.url},
            'organizer-apply.json':{'id':'apply','state':'completed','groups':[{'image_warnings':['Damaged image']}],'base':'private destination'},
        }
        for name,data in records.items():self.store.atomic_write(self.root/'data'/name,data)
        before={path.name:path.read_bytes() for path in (self.root/'data').iterdir() if path.is_file()}
        tasks=self.store.task_status()['tasks']
        self.assertEqual(set(tasks),{'download','organize_preview','organize_apply'})
        for task in tasks.values():
            self.assertEqual(set(task),{'id','state','started_at','finished_at','creator','needs_review','version'})
            self.assertGreater(int(task['version']),0)
        self.assertEqual(tasks['download']['finished_at'],'finish')
        self.assertTrue(tasks['download']['needs_review']);self.assertTrue(tasks['organize_apply']['needs_review'])
        self.assertFalse(tasks['organize_preview']['needs_review'])
        self.assertNotIn('private',json.dumps(tasks));self.assertNotIn(self.url,json.dumps(tasks))
        self.assertEqual(before,{path.name:path.read_bytes() for path in (self.root/'data').iterdir() if path.is_file()})

    def test_task_status_does_not_report_unreadable_state_as_completion(self):
        (self.root/'data/run.json').write_text('{partial')
        with self.assertRaises(ValueError):self.store.task_status()

    def test_review_panel_uses_only_current_batch_unresolved_errors(self):
        for mid,batch,error in ((1,'old','Old run failure'),(2,'current','Resolved failure'),(3,'current','Connection closed')):
            row=self.store.history.register(source_message_id=mid,creator='Example',topic_url=self.url,
                filename=f'Example 2026-09 {mid}.zip',bytes_total=20,batch_id=batch)
            with self.store.history.connect() as db:db.execute("UPDATE downloads SET state='paused',error=? WHERE id=?",(error,row['id']))
            if mid==2:self.store.history.complete(row['id'],destination=str(self.root/'done.zip'),verified_size=20,sha256='a'*64)
        run={'id':'current','state':'completed','plan_version':1,'resume_requested':True,
             'subscriptions':[self.entry],'warnings':['Old run failure','Resolved failure','Example: release month needs review for old.zip']}
        self.store.atomic_write(self.store.runs.path,run)
        queue=self.store.queue()
        self.assertEqual(queue['run']['warnings'],['Example · Example 2026-09 3.zip: Connection closed'])
        self.assertEqual(queue['worker_state'],'needs_review');self.assertTrue(queue['can_resume'])
        self.assertIn('1 / 2 complete',queue['run']['message'])
        self.assertEqual(json.loads(self.store.runs.path.read_text()),run)

    def test_saved_name_repairs_complete_normally_but_real_warnings_need_review(self):
        notice="release.zip: extracted 'bad:name.jpg' using safe name 'bad_name.jpg'."
        for problems in ([],['Damaged image skipped']):
            with self.subTest(problems=problems):
                run={'id':'run','state':'needs_review','warnings':[notice]+problems,'message':'Some items need review.'}
                job={'id':'job','state':'completed','groups':[{'image_warnings':[notice]+problems}]}
                self.store.atomic_write(self.root/'data/run.json',run)
                self.store.atomic_write(self.root/'data/organizer-apply.json',job)
                before={name:(self.root/'data'/name).read_bytes() for name in ('run.json','organizer-apply.json')}
                status=self.store.runs.status();tasks=self.store.task_status()['tasks']
                self.assertEqual(status['warnings'],problems)
                self.assertEqual(status['state'],'needs_review' if problems else 'completed')
                self.assertEqual(tasks['download']['state'],status['state'])
                self.assertEqual(tasks['download']['needs_review'],bool(problems))
                self.assertEqual(tasks['organize_apply']['needs_review'],bool(problems))
                if not problems:self.assertNotIn('review',status['message'])
                self.assertEqual(before,{name:(self.root/'data'/name).read_bytes() for name in before})

    def test_save_survives_reopen_and_rejects_stale_updates(self):
        saved = self.save()
        self.assertEqual(SubscriptionStore(configure_source(self.root)).read(),saved)
        with self.assertRaises(FileExistsError): self.save()
        self.assertEqual(self.store.read(),saved)
    def test_invalid_destinations_scopes_and_groups(self):
        for changes in [dict(creator_folder='../escape'),dict(download_scope=''),dict(download_scope='future'),dict(topic_url='https://t.me/c/9/123'),dict(layout='flat')]:
            with self.subTest(changes=changes),self.assertRaises(ValueError): self.save(**changes)
        self.assertEqual(self.store.read()['revision'],0)
    def test_release_pad_config_preserves_and_validates_destination(self):
        self.assertEqual(self.store.config()['release_pad_destination'],'')
        saved=self.store.save_config(dict(revision=0,download_directory=str(self.root),release_pad_destination=' https://t.me/example_release_pad/ '))
        self.assertEqual(saved['release_pad_destination'],'@example_release_pad')
        self.store.save_config(dict(revision=1,download_directory=str(self.root)))
        self.assertEqual(self.store.config()['release_pad_destination'],'@example_release_pad')
        for value in [None,123,'https://t.me/+invite','https://t.me/c/123/4','@example/123','--help','0','Release Pad']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.store.save_config(dict(revision=2,download_directory=str(self.root),release_pad_destination=value))
        self.store.save_config(dict(revision=2,download_directory=str(self.root),release_pad_destination='-1001234567890'))
        self.assertEqual(self.store.config()['release_pad_destination'],'-1001234567890')
        self.store.save_config(dict(revision=3,download_directory=str(self.root),release_pad_destination=''))
        self.assertEqual(self.store.config()['release_pad_destination'],'')

    def test_mmf_beta_defaults_off_and_preserves_saved_choice(self):
        self.assertFalse(self.store.config()['mmf_beta_enabled'])
        self.store.save_config(dict(revision=0,download_directory=str(self.root),mmf_beta_enabled=True))
        self.store.save_config(dict(revision=1,download_directory=str(self.root)))
        self.assertTrue(self.store.config()['mmf_beta_enabled'])
        with self.assertRaises(ValueError):
            self.store.save_config(dict(revision=2,download_directory=str(self.root),mmf_beta_enabled='false'))
        self.store.save_config(dict(revision=2,download_directory=str(self.root),mmf_beta_enabled=False))
        self.assertFalse(self.store.config()['mmf_beta_enabled'])

    def test_storage_choice_survives_reload_and_older_clients(self):
        self.assertEqual(self.store.config()['download_storage'],'local')
        saved=self.store.save_config(dict(revision=0,download_directory=str(self.root),download_storage='network'))
        self.assertEqual(saved['download_storage'],'network')
        self.store.save_config(dict(revision=1,download_directory=str(self.root)))
        self.assertEqual(self.store.config()['download_storage'],'network')
        self.store.save_config(dict(revision=2,download_directory=str(self.root),download_storage='local'))
        self.assertEqual(self.store.config()['download_storage'],'local')
        with self.assertRaises(ValueError):
            self.store.save_config(dict(revision=3,download_directory=str(self.root),download_storage='invalid'))

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
        self.assertEqual(self.store.config()['download_bandwidth_target_mbps'],28)
        self.store.save_config(dict(revision=0,download_directory=str(self.root),download_bandwidth_target_mbps=45.5))
        self.assertEqual(SubscriptionStore(self.root).config()['download_bandwidth_target_mbps'],45.5)
        self.store.save_config(dict(revision=1,download_directory=str(self.root)))
        self.assertEqual(self.store.config()['download_bandwidth_target_mbps'],45.5)
        before=self.store.config_path.read_bytes()
        for value in (0,-1,1001,10**1000,True,None,'30',float('nan'),float('inf')):
            with self.subTest(value=repr(value)),self.assertRaises(ValueError):
                self.store.save_config(dict(revision=2,download_directory=str(self.root),download_bandwidth_target_mbps=value))
            self.assertEqual(self.store.config_path.read_bytes(),before)

    def test_adaptive_setting_persists_and_old_forms_preserve_it(self):
        self.assertTrue(self.store.config()['adaptive_downloads'])
        self.store.save_config(dict(revision=0,download_directory=str(self.root),adaptive_downloads=False))
        self.store.save_config(dict(revision=1,download_directory=str(self.root)))
        self.assertFalse(self.store.config()['adaptive_downloads'])
        for value in (1,None,'false'):
            with self.assertRaises(ValueError):
                self.store.save_config(dict(revision=2,download_directory=str(self.root),adaptive_downloads=value))

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
