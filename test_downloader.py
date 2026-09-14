from test_support import configure_source
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import fcntl
import time
from unittest.mock import patch
from release_rules import release_month,in_scope,first_month,baseline_month
from file_delivery import deliver,DeliveryError,release_lock
from subscription_store import SubscriptionStore

class DownloaderTests(unittest.TestCase):
    def test_worker_status_and_stop_controls_are_independent(self):
        from folder_organizer import Organizer
        from organizer_apply import application_status
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store=SubscriptionStore(configure_source(root))
            store.atomic_write(store.runs.path,{'state':'downloading','message':'test'})
            store.atomic_write(root/'data/organizer-plan.json',{'state':'scanning','heartbeat':time.time()-100})
            store.atomic_write(root/'data/organizer-apply.json',{'state':'running','heartbeat':time.time()-100,'groups':[]})
            with (root/'data/worker.lock').open('a') as download,(root/'data/organizer-worker.lock').open('a') as organizer:
                fcntl.flock(download,fcntl.LOCK_EX|fcntl.LOCK_NB)
                self.assertEqual(store.runs.status()['state'],'downloading')
                self.assertEqual(Organizer(store).status()['state'],'interrupted')
                self.assertEqual(application_status(store)['state'],'interrupted')
                fcntl.flock(download,fcntl.LOCK_UN);fcntl.flock(organizer,fcntl.LOCK_EX|fcntl.LOCK_NB)
                self.assertEqual(store.runs.status()['state'],'interrupted')
                self.assertEqual(application_status(store)['state'],'running')
            store.runs.stop()
            self.assertTrue((root/'data/stop-request').exists());self.assertFalse((root/'data/organizer-stop').exists())
            (root/'data/stop-request').unlink();Organizer(store).stop()
            self.assertTrue((root/'data/organizer-stop').exists());self.assertFalse((root/'data/stop-request').exists())

    def test_bandwidth_counts_live_downloads_and_excludes_processing(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SubscriptionStore(configure_source(temp))
            files=[{'state':'downloading','download_finished_at':None,'download_speed_bps':15e6},
                   {'state':'downloading','download_finished_at':None,'download_speed_bps':14e6},
                   {'state':'downloading','download_finished_at':'finished','download_speed_bps':30e6},
                   {'state':'queued','download_finished_at':None,'download_speed_bps':30e6}]
            with patch.object(store.history,'queue',return_value={'files':files}),patch.object(store.runs,'status',return_value={'id':'batch','state':'downloading'}):
                status=store.queue()['bandwidth']
                self.assertEqual(status,{'target_mbps':28,'speed_bps':29e6,'active_files':2})
            for phase in ('extracting','copying','stopped'):
                with patch.object(store.history,'queue',return_value={'files':files}),patch.object(store.runs,'status',return_value={'id':'batch','state':phase}):
                    self.assertEqual(store.queue()['bandwidth'],{'target_mbps':28,'speed_bps':None,'active_files':0})

    def test_adaptive_speed_stays_visible_during_processing_and_ignores_old_batches(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SubscriptionStore(configure_source(temp))
            status={'batch_id':'batch','active':True,'target_mbps':28,'transfers':[
                {'phase':'downloading','speed_bps':9e6},{'phase':'downloading','speed_bps':19e6},
                {'phase':'verifying','speed_bps':20e6}]}
            store.atomic_write(store.root/'data/download-scheduler.json',status)
            run={'id':'batch','state':'extracting','adaptive_scheduler':True}
            with patch.object(store.history,'queue',return_value={'files':[]}),patch.object(store.runs,'status',return_value=run):
                result=store.queue();self.assertIsNotNone(result['scheduler'])
                self.assertEqual(result['bandwidth'],{'target_mbps':28,'speed_bps':28e6,'active_files':2})
                run['id']='another-batch';self.assertIsNone(store.queue()['scheduler'])
                run.update(id='batch',state='stopped');self.assertIsNone(store.queue()['scheduler'])

    def test_waiting_on_same_month_can_stop_without_releasing_other_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cancelled=False
            def waiting():
                nonlocal cancelled
                cancelled=True
            with release_lock(root,root,'Example','2026-09'):
                with release_lock(root,root,'Example','2026-10'):pass
                with self.assertRaisesRegex(DeliveryError,'Stopped'):
                    with release_lock(root,root,'example','2026-09',lambda:cancelled,waiting):self.fail('Concurrent owner entered')
                with self.assertRaisesRegex(DeliveryError,'Stopped'):
                    with release_lock(root,root,'Example','2026-09',lambda:cancelled):self.fail('Other owner lost its lock')
            with release_lock(root,root,'Example','2026-09'):pass

    def test_release_month_not_posting_month_and_parts(self):
        self.assertEqual(release_month('Atlan Forge - 2026-07.7z.002'),'2026-07')
        self.assertEqual(release_month('Atlan Forge - 24-07 - Loyalty.7z.001'),'2024-07')
        self.assertEqual(release_month('Atlan Forge - 24-07.7z.001'),'2024-07')
        self.assertIsNone(release_month('Archive part 001.7z.002'))
        self.assertEqual(release_month('Example September 2026.zip'),'2026-09')
        self.assertEqual(release_month('Atlan Forge - September 2024.7z'),'2024-09')
        self.assertIsNone(release_month('Example 2024.7z'))
        self.assertEqual(release_month('Example 2026-August.rar'),'2026-08')
        self.assertIsNone(release_month('Loyalty.7z'))
        self.assertIsNone(release_month('2026-07 and 2026-08.zip'))
        for filename,expected in [('Example 02-22.zip','2022-02'),('Example 09.22.rar','2022-09'),
                                  ('Example_03_23_release.zip','2023-03'),('Example 01-02.zip',None),
                                  ('Example 00-22.zip',None),('Example 13-22.zip',None)]:
            with self.subTest(filename=filename):self.assertEqual(release_month(filename),expected)
        for filename,expected in [('Example 01-2023.zip','2023-01'),('Example 09.2022.rar','2022-09'),
                                  ('Example_03_2023_Release.zip','2023-03'),('Example 04-2023 - April 23.zip','2023-04'),
                                  ('Example 2023-01.zip','2023-01'),('Example 3_2023.zip','2023-03'),
                                  ('Example 13-2023.zip',None),('Example 00.2022.zip',None),
                                  ('Example 103_2023.zip',None),('Example 03_20230.zip',None),
                                  ('Example 01-2023 and 02-2023.zip',None)]:
            with self.subTest(filename=filename):self.assertEqual(release_month(filename),expected)
        sub={'download_scope':'future','start_month':'2026-09'}
        self.assertTrue(in_scope(sub,'2026-09'))
        self.assertTrue(in_scope(sub,'2026-10'))
        self.assertFalse(in_scope(sub,'2026-08'))
        self.assertEqual(first_month('2026-08-31T22:30:00+00:00'),'2026-09')
        self.assertEqual(baseline_month('2026-09-12T12:00:00+00:00'),'2026-08')
        self.assertEqual(baseline_month('2026-01-12T12:00:00+00:00'),'2025-12')
    def test_verified_delivery_does_not_overwrite_or_delete_source(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);source=base/'source.zip';source.write_bytes(b'valid test data')
            target,size,checksum=deliver(source,base,'Example','2026-09','file.zip')
            self.assertEqual(target.read_bytes(),source.read_bytes())
            self.assertTrue(source.exists()) # worker deletes only after DB commit
            self.assertEqual(deliver(source,base,'Example','2026-09','file.zip')[2],checksum)
            source.write_bytes(b'different')
            with self.assertRaises(DeliveryError):deliver(source,base,'Example','2026-09','file.zip')
            self.assertEqual(target.read_bytes(),b'valid test data')
    def test_interrupted_delivery_keeps_source(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);source=base/'source.zip';source.write_bytes(b'valid test data')
            def cancel(*_):raise RuntimeError('cancel')
            with self.assertRaises(RuntimeError):deliver(source,base,'Example','2026-09','file.zip',cancel)
            self.assertTrue(source.exists())
            self.assertFalse((base/'Example/2026-09/file.zip').exists())
            self.assertEqual(list((base/'Example/2026-09').iterdir()),[])
    def test_mount_change_keeps_source(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);source=base/'source.zip';source.write_bytes(b'test')
            with patch('file_delivery.mount_identity',side_effect=[('nas',1),('local',2)]),self.assertRaises(DeliveryError):deliver(source,base,'Example','2026-09','file.zip')
            self.assertTrue(source.exists())
    def test_saving_or_reading_does_not_start_a_worker(self):
        with tempfile.TemporaryDirectory() as temp,patch('run_manager.subprocess.Popen') as launch:
            store=SubscriptionStore(configure_source(temp))
            self.assertEqual(store.queue()['worker_state'],'idle')
            self.assertFalse(store.queue()['can_start'])
            with self.assertRaises(ValueError):store.runs.start({'revision':0,'config_revision':0})
            launch.assert_not_called()
    def test_manual_start_snapshots_saved_settings_and_blocks_double_click(self):
        with tempfile.TemporaryDirectory() as temp,patch('run_manager.subprocess.Popen') as launch:
            store=SubscriptionStore(configure_source(temp))
            store.atomic_write(store.path,{'version':2,'revision':1,'saved_at':'2026-09-12T12:00:00+00:00','subscriptions':[{'topic_url':'https://t.me/c/123456789/200','download_scope':'future'}]})
            run=store.runs.start({'revision':1,'config_revision':0})
            self.assertEqual(run['subscriptions'][0]['start_month'],'2026-08')
            launch.assert_called_once()
            with self.assertRaises(FileExistsError):store.runs.start({'revision':1,'config_revision':0})
            store.runs.stop();self.assertTrue((Path(temp)/'data/stop-request').exists())

if __name__=='__main__':unittest.main()

class ResumeTests(unittest.TestCase):
    def finish_fake_transfer(self,store,root,transfers):
        def transfer(sub,item,month):
            transfers.append(item['source_message_id'])
            row=store.history.register(source_message_id=item['source_message_id'],creator=sub['creator'],topic_url=sub['topic_url'],filename=item['filename'],bytes_total=item['bytes_total'])
            store.history.complete(row['id'],destination=str(root/'delivered'/item['filename']),verified_size=item['bytes_total'],sha256='0'*64)
        return transfer

    def test_saved_queue_skips_direct_images_without_rescanning_or_claiming_downloads(self):
        import download_worker
        from download_plan import DownloadPlan
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store,state,run=self.fixture(root);a,b=run['subscriptions'];transfers=[]
            archive=self.item(a,201);pending={**self.item(a,202),'filename':'Example A 2026-09.JPG'}
            finished={**self.item(a,203),'filename':'Example A 2026-09.png'}
            typed={**self.item(b,301),'filename':'Example B 2026-09 preview','mime_type':'image/jpeg'}
            rows={}
            for item in (pending,finished):
                rows[item['source_message_id']]=store.history.register(source_message_id=item['source_message_id'],creator=a['creator'],topic_url=a['topic_url'],filename=item['filename'],bytes_total=item['bytes_total'],batch_id=run['id'])
            store.history.complete(rows[203]['id'],destination=str(root/'existing-image.png'),verified_size=123,sha256='0'*64)
            plan=DownloadPlan(store,run)
            for sub,items in ((a,[archive,pending,finished]),(b,[typed])):
                plan.save(sub,[{'item':item,'month':'2026-09'} for item in items],{'creator':sub['creator'],'eligible_files':len(items)},[])
            class CLI:
                def list_files(inner,*args,**kwargs):raise AssertionError('Saved artist checks must be reused')
                def close(inner):pass
            with patch.object(download_worker,'ROOT',root),patch.object(download_worker,'STATE',state),patch.object(download_worker,'TelegramCLI',return_value=CLI()),patch.object(download_worker.Worker,'transfer',side_effect=self.finish_fake_transfer(store,root,transfers)):
                worker=download_worker.Worker(run['id']);worker.execute()
            self.assertEqual(worker.run['state'],'completed',worker.run['message']);self.assertEqual(transfers,[201])
            self.assertEqual(sum(r['ignored_images'] for r in worker.run['creator_results']),3)
            with store.history.connect() as db:
                excluded=dict(db.execute('SELECT * FROM downloads WHERE id=?',(rows[202]['id'],)).fetchone())
                kept=dict(db.execute('SELECT * FROM downloads WHERE id=?',(rows[203]['id'],)).fetchone())
                self.assertIsNone(db.execute('SELECT * FROM downloads WHERE source_message_id=301').fetchone())
            self.assertEqual(excluded['state'],'paused');self.assertEqual(excluded['batch_id'],'excluded-images-'+run['id']);self.assertIn('excluded',excluded['error'])
            self.assertEqual(kept['state'],'downloaded');self.assertEqual(kept['batch_id'],run['id'])
            self.assertEqual(store.history.queue(run['id'])['total_files'],2)

    def test_new_scan_skips_image_documents_before_month_review_or_download_registration(self):
        import download_worker
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store,state,run=self.fixture(root);transfers=[]
            class CLI:
                def list_files(inner,topic,*args,**kwargs):
                    sub=next(s for s in run['subscriptions'] if s['topic_url']==topic)
                    mid=201 if sub==run['subscriptions'][0] else 301
                    return [self.item(sub,mid),{**self.item(sub,mid+1),'filename':'undated-preview.JPEG'},{**self.item(sub,mid+2),'filename':'unnamed preview','mime_type':'image/png'}]
                def close(inner):pass
            with patch.object(download_worker,'ROOT',root),patch.object(download_worker,'STATE',state),patch.object(download_worker,'TelegramCLI',return_value=CLI()),patch.object(download_worker.Worker,'transfer',side_effect=self.finish_fake_transfer(store,root,transfers)):
                worker=download_worker.Worker(run['id']);worker.execute()
            self.assertEqual(worker.run['state'],'completed',worker.run['message']);self.assertEqual(transfers,[201,301])
            self.assertEqual(worker.run['warnings'],[]);self.assertEqual(sum(r['ignored_images'] for r in worker.run['creator_results']),4)
            self.assertEqual(store.history.queue(run['id'])['total_files'],2)

    def fixture(self, root):
        store=SubscriptionStore(configure_source(root));base=root/'destination';base.mkdir()
        state=root/'telegram';state.mkdir()
        subs=[{'creator':name,'creator_folder':name,'topic_url':f'https://t.me/c/123456789/{topic}',
               'download_scope':'from_month','start_month':'2026-09'} for name,topic in [('Example A',200),('Example B',300)]]
        run={'id':'resume-test','state':'starting','started_at':'2026-09-12T12:00:00+00:00','subscriptions':subs,
             'download_directory':str(base),'warnings':[],'creators_done':0,'plan_version':1}
        store.atomic_write(store.runs.path,run)
        store.atomic_write(store.config_path,{'revision':1,'download_directory':str(base),'server_speed_threshold_mbps':20})
        return store,state,run

    def item(self, sub, mid):
        return {'filename':sub['creator']+' 2026-09.zip','source_message_id':mid,'topic_url':sub['topic_url'],
                'message_url':sub['topic_url']+'/'+str(mid),'bytes_total':123,'dc_id':4,'document_id':mid+1000}

    def test_resume_keeps_batch_and_saved_selection_with_current_server_settings(self):
        with tempfile.TemporaryDirectory() as temp,patch('run_manager.subprocess.Popen') as launch:
            root=Path(temp);store,state,run=self.fixture(root)
            run.update(state='stopped',finished_at='2026-09-12T13:00:00+00:00')
            store.atomic_write(store.runs.path,run);store.runs.stop()
            # A later subscription edit must not change this saved run.
            store.atomic_write(store.path,{'revision':2,'subscriptions':[]})
            resumed=store.runs.resume({'run_id':run['id']})
            self.assertEqual(resumed['id'],run['id']);self.assertEqual(resumed['subscriptions'],run['subscriptions'])
            self.assertEqual(resumed['config']['server_speed_threshold_mbps'],20)
            self.assertEqual(resumed['resume_count'],1);self.assertNotIn('finished_at',resumed)
            self.assertFalse((root/'data/stop-request').exists());launch.assert_called_once()
            self.assertEqual(store.runs.status()['state'],'starting') # fresh grace, despite old started_at
            with self.assertRaises(FileExistsError):store.runs.resume({'run_id':run['id']})
            launch.assert_called_once()

    def test_resume_rejects_stale_run_busy_worker_changed_folder_and_source(self):
        with tempfile.TemporaryDirectory() as temp,patch('run_manager.subprocess.Popen') as launch:
            root=Path(temp);store,state,run=self.fixture(root);run['state']='failed';store.atomic_write(store.runs.path,run)
            with self.assertRaises(FileExistsError):store.runs.resume({'run_id':'old-run'})
            with (root/'data/worker.lock').open('a') as worker:
                fcntl.flock(worker,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(FileExistsError,'finishing'):store.runs.resume({'run_id':run['id']})
            config=store.config();store.atomic_write(store.config_path,{**config,'download_directory':str(root)})
            with self.assertRaisesRegex(ValueError,'destination changed'):store.runs.resume({'run_id':run['id']})
            store.atomic_write(store.config_path,config)
            store.atomic_write(root/'data/source.json',{'chat_id':987654321,'toc_message_id':100})
            with self.assertRaisesRegex(ValueError,'another Telegram source'):store.runs.resume({'run_id':run['id']})
            launch.assert_not_called()

    def test_launch_failure_keeps_resumable_queue(self):
        with tempfile.TemporaryDirectory() as temp,patch('run_manager.subprocess.Popen',side_effect=OSError('fixture')):
            root=Path(temp);store,state,run=self.fixture(root);run['state']='stopped';store.atomic_write(store.runs.path,run)
            with self.assertRaisesRegex(ValueError,'saved queue was kept'):store.runs.resume({'run_id':run['id']})
            saved=store.runs.status();self.assertEqual(saved['state'],'failed');self.assertEqual(saved['id'],run['id'])
            self.assertTrue(store.queue()['can_resume'])

    def test_resume_only_scans_unfinished_artist_and_skips_completed_download(self):
        import download_worker
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store,state,run=self.fixture(root);a,b=run['subscriptions'];calls=[];transfers=[]
            class CLI:
                def list_files(inner,topic,stopped,status,**kwargs):
                    calls.append(topic)
                    if topic==b['topic_url']:
                        store.runs.stop();raise download_worker.WorkerError('Stopped during metadata scan')
                    return [self.item(a,201)]
                def close(inner):pass
            def transfer(worker,sub,item,month):
                transfers.append(item['source_message_id']);row=store.history.register(source_message_id=item['source_message_id'],creator=sub['creator'],
                    topic_url=sub['topic_url'],filename=item['filename'],bytes_total=item['bytes_total'])
                store.history.complete(row['id'],destination=str(root/'moved-away.zip'),verified_size=123,sha256='0'*64)
            with patch.object(download_worker,'ROOT',root),patch.object(download_worker,'STATE',state),patch.object(download_worker,'TelegramCLI',return_value=CLI()):
                worker=download_worker.Worker(run['id']);worker.execute()
                self.assertEqual(worker.run['state'],'stopped');self.assertEqual(worker.run['creators_checked'],1)
                self.assertIsNotNone(worker.plan.load(a));self.assertIsNone(worker.plan.load(b))
                # The organizer can finish A while the downloader is stopped.
                transfer(worker,a,self.item(a,201),'2026-09');transfers.clear();calls.clear()
                with patch('run_manager.subprocess.Popen'):store.runs.resume({'run_id':run['id']})
                cli=CLI()
                def list_remaining(topic,*args,**kwargs):
                    calls.append(topic);self.assertEqual(topic,b['topic_url']);return [self.item(b,301)]
                with patch.object(cli,'list_files',side_effect=list_remaining),patch.object(download_worker,'TelegramCLI',return_value=cli),patch.object(download_worker.Worker,'transfer',transfer):
                    resumed=download_worker.Worker(run['id']);resumed.execute()
                self.assertEqual(resumed.run['state'],'completed',resumed.run['message'])
                self.assertEqual(calls,[b['topic_url']]);self.assertEqual(transfers,[301]);self.assertEqual(resumed.run['creators_done'],2)

    def test_failure_after_all_checks_resumes_without_any_artist_scan(self):
        import download_worker
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store,state,run=self.fixture(root);subs=run['subscriptions'];calls=[];transfers=[]
            class CLI:
                def list_files(inner,topic,*args,**kwargs):
                    calls.append(topic);index=[s['topic_url'] for s in subs].index(topic);return [self.item(subs[index],201+index*100)]
                def close(inner):pass
            cli=CLI()
            with patch.object(download_worker,'ROOT',root),patch.object(download_worker,'STATE',state),patch.object(download_worker,'TelegramCLI',return_value=cli):
                with patch.object(download_worker.Worker,'transfer',side_effect=RuntimeError('Transfer interrupted')):
                    worker=download_worker.Worker(run['id']);worker.execute()
                self.assertEqual(worker.run['state'],'failed');self.assertEqual(worker.run['creators_checked'],2)
                with patch('run_manager.subprocess.Popen'):store.runs.resume({'run_id':run['id']})
                with patch.object(cli,'list_files',side_effect=AssertionError('Already checked')),patch.object(download_worker.Worker,'transfer',side_effect=self.finish_fake_transfer(store,root,transfers)):
                    resumed=download_worker.Worker(run['id']);resumed.execute()
                self.assertEqual(resumed.run['state'],'completed',resumed.run['message']);self.assertEqual(transfers,[201,301])

    def test_legacy_checkpoints_use_exact_old_queue_not_newer_catalog_posts(self):
        from download_plan import DownloadPlan
        from topic_catalog import TopicCatalog
        from test_support import TEST_SOURCE
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store,state,run=self.fixture(root);a,b=run['subscriptions'];run.pop('plan_version')
            run.update(creators_checked=1,creator_results=[{'creator':a['creator']},{'creator':b['creator']}])
            item=self.item(a,201);newer={**self.item(a,202),'filename':'Example A 2026-09 newer.zip'}
            TopicCatalog(root,TEST_SOURCE).commit(a['topic_url'],0,202,[item,newer])
            for sub,mid in [(a,201),(b,301)]:
                f=self.item(sub,mid);store.history.register(source_message_id=mid,creator=sub['creator'],topic_url=sub['topic_url'],
                    filename=f['filename'],bytes_total=f['bytes_total'],release_month='2026-09',batch_id=run['id'])
            plan=DownloadPlan(store,run);plan.seed_legacy()
            self.assertEqual([r['item']['source_message_id'] for r in plan.load(a)['items']],[201])
            self.assertIsNone(plan.load(b)) # incomplete scan, even with a partial result
            run['creators_checked']=2;plan.seed_legacy();self.assertIsNone(plan.load(b)) # missing metadata requires only B to be checked

    def test_checkpoint_rejects_changed_scope_or_attachment_identity(self):
        from download_plan import DownloadPlan
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store,state,run=self.fixture(root);sub=run['subscriptions'][0];plan=DownloadPlan(store,run)
            plan.save(sub,[{'item':self.item(sub,201),'month':'2026-09'}],{'creator':sub['creator']},[])
            original=json.loads(plan.path(sub).read_text())
            changed=json.loads(json.dumps(original));changed['items'][0]['item']['message_url']='https://t.me/c/987654321/200/201'
            store.atomic_write(plan.path(sub),changed)
            with self.assertRaises(ValueError):plan.load(sub)
            store.atomic_write(plan.path(sub),original);sub['start_month']='2026-10'
            with self.assertRaises(ValueError):DownloadPlan(store,run).load(sub)


class WorkerTests(unittest.TestCase):
    def test_batch_moves_file_records_history_and_skips_completed_id(self):
        import download_worker
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);state=root/'telegram';state.mkdir();base=root/'destination';base.mkdir()
            store=SubscriptionStore(configure_source(root))
            topic='https://t.me/c/123456789/200'
            sub={'creator':'Example','topic_url':topic,'creator_folder':'Example','download_scope':'future','start_month':'2026-09'}
            store.atomic_write(root/'data/run.json',{'id':'test','state':'starting','subscriptions':[sub],'download_directory':str(base),'warnings':[],'creators_done':0})
            old=store.history.register(source_message_id=1,creator='Example',topic_url=topic,filename='Example 2026-09 A.zip',bytes_total=1)
            store.history.complete(old['id'],destination=str(base/'moved-away.zip'),verified_size=1,sha256='0'*64)
            fixture=state/'fixture.zip'
            with zipfile.ZipFile(fixture,'w') as archive:
                archive.writestr('Pictures/preview.JPG',b'original image bytes')
                archive.writestr('Model.stl',b'model bytes')
            class FakeUI:
                def __init__(self):self.calls=[]
                def list_files(self,topic,stopped,status,**kwargs):
                    return [{'filename':'Example 2026-09 A.zip','source_message_id':1,'message_url':topic+'/1','bytes_total':1},
                            {'filename':'Example 2026-09 B.zip','source_message_id':2,'message_url':topic+'/2','bytes_total':fixture.stat().st_size}]
                def download(self,item,progress,stopped,status=None):
                    self.calls.append(item['source_message_id']);source=state/item['filename']
                    source.write_bytes(fixture.read_bytes())
                    progress(1,source.stat().st_size)
                    with store.history.connect() as db:
                        row=db.execute('SELECT * FROM downloads WHERE source_message_id=2').fetchone()
                        assert row['bytes_total']==fixture.stat().st_size and row['download_finished_at'] is None
                    progress(source.stat().st_size,source.stat().st_size);return source
                def scroll_files(self):pass
                def close(self):pass
            ui=FakeUI()
            with patch.object(download_worker,'ROOT',root),patch.object(download_worker,'STATE',state),patch.object(download_worker,'TelegramCLI',return_value=ui):
                worker=download_worker.Worker('test');worker.execute()
            self.assertEqual(ui.calls,[2])
            with zipfile.ZipFile(base/'Example/2026-09/Example 2026-09 B.zip') as archive:
                self.assertEqual(archive.read('Model.stl'),b'model bytes')
            images=list((base/'Example/2026-09/release_images').rglob('*.JPG'))
            self.assertEqual(len(images),1)
            self.assertEqual(images[0].read_bytes(),b'original image bytes')
            self.assertFalse((state/'Example 2026-09 B.zip').exists())
            self.assertTrue(store.history.was_downloaded(2))
            with store.history.connect() as db:
                row=db.execute('SELECT image_count,images_manifest FROM downloads WHERE source_message_id=2').fetchone()
                self.assertEqual(row['image_count'],1)
                self.assertEqual(json.loads(row['images_manifest'])[0]['size'],20)
            self.assertEqual(json.loads((root/'data/run.json').read_text())['state'],'completed')
