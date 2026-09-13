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
                def list_files(self,topic,stopped,status):
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
