import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
from availability import Availability,INTERVAL,eligible
from subscription_store import SubscriptionStore
from test_support import configure_source

TOPIC='https://t.me/c/123456789/200'
SUB={'creator':'Example','topic_url':TOPIC,'download_scope':'from_month','start_month':'2026-08','subscribed_at':'2026-09-01T00:00:00+00:00'}

def item(mid,name):return {'source_message_id':mid,'filename':name,'bytes_total':100,'topic_url':TOPIC}

class AvailabilityTests(unittest.TestCase):
    def setup(self,root):
        store=SubscriptionStore(configure_source(root))
        store.atomic_write(store.path,{'revision':1,'subscriptions':[SUB],'saved_at':SUB['subscribed_at']})
        store.queue=Mock(return_value={'active':False,'organizer_active':False})
        return store,Availability(store)

    def test_configured_interval_recalculates_due_time_from_last_check(self):
        with tempfile.TemporaryDirectory() as temp:
            store,checker=self.setup(Path(temp))
            store.atomic_write(checker.path,{'subscriptions':[SUB],'items':[],'checked_at':1000,'next_check_at':15400})
            for revision,hours in enumerate((1,8,.25)):
                store.save_config({'revision':revision,'download_directory':str(store.root),'availability_interval_hours':hours})
                self.assertEqual(checker.status()['next_check_at'],1000+hours*3600)
                self.assertEqual(checker.status()['interval_seconds'],hours*3600)
            store.save_config({'revision':3,'download_directory':str(store.root)})
            self.assertEqual(store.config()['availability_interval_hours'],.25)
            for value in (0,-1,169,True,'4',float('nan'),float('inf')):
                with self.subTest(value=value),self.assertRaises(ValueError):
                    store.save_config({'revision':4,'download_directory':str(store.root),'availability_interval_hours':value})

    def test_scope_images_and_preferred_upload(self):
        files=[item(1,'Example 2026-07.zip'),item(2,'Example 2026-08.jpg'),item(3,'unknown.zip'),item(4,'Example 2026-08.zip'),item(5,'Example 2026-08.zip')]
        self.assertEqual([i['source_message_id'] for i in eligible(SUB,files)],[5])

    def test_check_only_metadata_and_completed_or_ignored_disappear(self):
        with tempfile.TemporaryDirectory() as temp:
            store,checker=self.setup(Path(temp));client=Mock()
            client.list_files.return_value=[item(4,'Example 2026-08.zip'),item(6,'Example 2026-09.zip')]
            with patch('availability.TelegramCLI',return_value=client),patch('availability.time.time',return_value=1000):checker.check()
            client.list_files.assert_called_once_with(TOPIC,incremental=True)
            self.assertEqual([c[0] for c in client.method_calls],['list_files','close'])
            state=checker.status();self.assertEqual(state['files'],2);self.assertEqual(state['next_check_at'],1000+INTERVAL)
            with store.history.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM downloads').fetchone()[0],0)
            for mid,name in [(4,'Example 2026-08.zip'),(6,'Example 2026-09.zip')]:
                store.history.register(source_message_id=mid,creator='Example',topic_url=TOPIC,filename=name,bytes_total=100)
            with store.history.connect() as db:
                db.execute("UPDATE downloads SET state='downloaded' WHERE source_message_id=4")
                db.execute("UPDATE downloads SET ignored_at='now' WHERE source_message_id=6")
            self.assertEqual(checker.status()['files'],0)

    def test_schedule_survives_reload_and_multiple_tabs_do_not_duplicate_check(self):
        with tempfile.TemporaryDirectory() as temp:
            store,checker=self.setup(Path(temp))
            with patch('availability.threading.Thread') as thread:
                checker.start();checker.start();self.assertEqual(thread.call_count,1)
            store.atomic_write(checker.path,{'subscriptions':[SUB],'items':[],'checked_at':1000,'next_check_at':1000+INTERVAL})
            checker=Availability(store)
            with patch('availability.time.time',return_value=1001),patch('availability.threading.Thread') as thread:
                checker.start();thread.assert_not_called()
            store.queue.return_value['active']=True
            with patch('availability.time.time',return_value=20000),patch('availability.threading.Thread') as thread:
                self.assertTrue(checker.start()['deferred']);thread.assert_not_called()

    def test_failed_scan_does_not_report_all_clear_and_changed_scope_invalidates(self):
        with tempfile.TemporaryDirectory() as temp:
            store,checker=self.setup(Path(temp))
            with patch('availability.TelegramCLI',side_effect=RuntimeError('offline')):checker.check()
            self.assertTrue(checker.status()['errors']);self.assertFalse(checker.running)
            changed={**SUB,'start_month':'2026-07'}
            store.atomic_write(store.path,{'revision':2,'subscriptions':[changed],'saved_at':SUB['subscribed_at']})
            self.assertEqual(checker.status()['next_check_at'],0)

    def test_detected_run_uses_exact_snapshot_without_scanning_other_subscriptions(self):
        from download_plan import DownloadPlan
        with tempfile.TemporaryDirectory() as temp:
            store,checker=self.setup(Path(temp))
            other={**SUB,'creator':'Other','topic_url':'https://t.me/c/123456789/300'}
            subs=[SUB,other]
            store.atomic_write(store.path,{'revision':1,'subscriptions':subs,'saved_at':SUB['subscribed_at']})
            files=[{**item(n,f'Example 2026-0{n}.zip'),'month':f'2026-0{n}',
                    'message_url':TOPIC+'/'+str(n)} for n in (8,9)]
            store.atomic_write(checker.path,{'subscriptions':subs,'items':files,'checked_at':1000})
            store.history.register(source_message_id=8,creator='Example',topic_url=TOPIC,filename=files[0]['filename'],bytes_total=100)
            with store.history.connect() as db:db.execute("UPDATE downloads SET state='downloaded' WHERE source_message_id=8")
            with patch('run_manager.subprocess.Popen'),patch('availability.TelegramCLI',side_effect=AssertionError('Must not scan')):
                run=store.runs.start({'revision':1,'config_revision':store.config()['revision'],'mode':'detected'})
            self.assertEqual(run['subscriptions'],[SUB]);self.assertEqual(run['creators_total'],1)
            checkpoint=DownloadPlan(store,run).load(SUB)
            self.assertEqual([e['item']['source_message_id'] for e in checkpoint['items']],[9])
            self.assertEqual(run['availability_checked_at'],1000)
            self.assertTrue(checker.status()['claimed']);self.assertEqual(checker.status()['files'],0)
            self.assertEqual(checker.status()['next_check_at'],1000+INTERVAL)
            # A newer check may notify about newly detected files again.
            store.atomic_write(checker.path,{'subscriptions':subs,'items':files,'checked_at':2000})
            self.assertFalse(checker.status()['claimed']);self.assertEqual(checker.status()['files'],1)
            # Explicit all mode must not reuse even a valid detection snapshot.
            store.atomic_write(store.runs.path,{**run,'state':'stopped'})
            with patch('run_manager.subprocess.Popen'):
                fresh=store.runs.start({'revision':1,'config_revision':store.config()['revision'],'mode':'all'})
            self.assertEqual(fresh['subscriptions'],subs)
            self.assertIsNone(DownloadPlan(store,fresh).load(SUB))

    def test_detected_never_silently_falls_back_to_a_full_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            store,checker=self.setup(Path(temp))
            payload={'revision':1,'config_revision':store.config()['revision'],'mode':'detected'}
            with patch('run_manager.subprocess.Popen') as launch:
                with self.assertRaisesRegex(ValueError,'no longer match'):store.runs.start(payload)
                store.atomic_write(checker.path,{'subscriptions':[SUB],'items':[],'checked_at':1000})
                with self.assertRaisesRegex(ValueError,'No known'):store.runs.start(payload)
                store.atomic_write(checker.path,{'subscriptions':[SUB],'items':[{'topic_url':TOPIC,'source_message_id':9,'month':'2026-09'}],'checked_at':1000})
                with self.assertRaisesRegex(ValueError,'metadata is missing'):store.runs.start(payload)
                launch.assert_not_called()
