from test_support import configure_source
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from folder_organizer import inventory, match_files, Organizer
from organizer_apply import ApplyWorker, PinnedFolder, ready_groups, start_application, check_parts
from release_images import flat_image_name
from file_delivery import digest
from subscription_store import SubscriptionStore
from test_release_images import damaged_image_zip

TOPIC='https://t.me/c/123456789/200'


class OrganizerApplyTests(unittest.TestCase):
    def repair_fixture(self, root, monthly=False, missing=False):
        store,folder=self.setup_store(root);image=root/'preview.jpg';image.write_bytes(os.urandom(6000))
        subprocess.run(['7z','a','-mx0','-v4k',str(folder/'Example 2026-01.7z'),str(image)],stdout=subprocess.DEVNULL,check=True)
        first=folder/'Example 2026-01.7z.001';full=first.read_bytes();first.write_bytes(b'incomplete original')
        if monthly:
            (folder/'2026-01').mkdir();first=first.rename(folder/'2026-01'/first.name)
        plan=self.preview(store,folder)
        for item in plan['attachments']:
            item.update(topic_url=TOPIC,dc_id=2,document_id=123456+item['source_message_id'])
            if item['filename']==first.name:item['bytes_total']=len(full)
        if missing:
            first.unlink();plan['files']=[f for f in plan['files'] if f['filename']!=first.name]
        match_files(plan['files'],plan['attachments'],set());store.atomic_write(Organizer(store).path,plan)
        job_id=self.start(store);ApplyWorker(store,job_id).execute()
        self.assertEqual(self.job(store)['releases_review'],1)
        source=root/'cli-transfer/payload.part';source.parent.mkdir();source.write_bytes(full)
        class Client:
            calls=0
            def download(self,item,progress,stopped,status):
                self.calls+=1;status({'event':'download_start'});progress(0,len(full));progress(len(full),len(full));return source
            def close(self):pass
            def cleanup_transfer(self,item):pass
        return store,folder,job_id,first,full,image,source,Client()

    def request_repair(self, store, job_id):
        with patch('organizer_apply.Popen'):start_application(store,{'job_id':job_id},resume=True,repair=True)

    def test_manual_repair_downloads_exact_part_backs_up_original_and_finishes_release(self):
        for missing in (False,True):
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as temporary:
                store,folder,job_id,first,full,image,source,client=self.repair_fixture(Path(temporary),missing=missing)
                options=Organizer(store).status()['application']['repair_files']
                self.assertEqual(len(options),1);self.assertEqual(options[0]['bytes_total'],len(full))
                with patch('organizer_repair.TelegramCLI',return_value=client):
                    with patch('organizer_apply.Popen'):start_application(store,{'job_id':job_id},resume=True)
                    ApplyWorker(store,job_id).execute();self.assertEqual(client.calls,0) # Ordinary Retry never downloads.
                    self.request_repair(store,job_id);ApplyWorker(store,job_id).execute()
                job=self.job(store);self.assertEqual((job['state'],job['files_done'],job['files_total'],job['releases_review']),('completed',2,2,0),job['message'])
                self.assertEqual(client.calls,1);self.assertFalse(first.exists());self.assertFalse(source.exists())
                target=folder/'2026-01'/first.name;self.assertEqual(target.read_bytes(),full)
                self.assertEqual(next((folder/'2026-01/release_images').iterdir()).read_bytes(),image.read_bytes())
                repair=job['groups'][0]['repairs'][0]
                if not missing:self.assertEqual(Path(repair['backup']['path']).read_bytes(),b'incomplete original')
                with store.history.connect() as db:
                    row=dict(db.execute('SELECT * FROM downloads WHERE filename=?',(first.name,)).fetchone())
                self.assertEqual(row['state'],'downloaded');self.assertEqual(row['sha256'],digest(target));self.assertEqual(row['origin'],'download')
                self.assertIsNotNone(row['download_seconds']);self.assertEqual(row['image_count'],1)

    def test_repair_delivery_failure_reuses_download_and_preserves_local_backup(self):
        for after_publish in (False,True):
            with self.subTest(after_publish=after_publish),tempfile.TemporaryDirectory() as temporary:
                store,folder,job_id,first,full,image,source,client=self.repair_fixture(Path(temporary),monthly=after_publish)
                self.request_repair(store,job_id)
                from file_delivery import deliver
                def fail_delivery(*args,**kwargs):
                    if after_publish:deliver(*args,**kwargs)
                    raise OSError('test interrupted delivery')
                with patch('organizer_repair.TelegramCLI',return_value=client):
                    with patch('organizer_repair.deliver',side_effect=fail_delivery):ApplyWorker(store,job_id).execute()
                    failed=self.job(store);repair=failed['groups'][0]['repairs'][0]
                    self.assertEqual(failed['releases_review'],1);self.assertFalse(store.history.was_downloaded(repair['item']['source_message_id']))
                    self.assertEqual(Path(repair['backup']['path']).read_bytes(),b'incomplete original');self.assertTrue(source.exists())
                    if not after_publish:self.assertEqual(first.read_bytes(),b'incomplete original')
                    with patch('organizer_apply.Popen'):start_application(store,{'job_id':job_id},resume=True)
                    ApplyWorker(store,job_id).execute()
                self.assertEqual(client.calls,1);self.assertEqual(self.job(store)['releases_review'],0,self.job(store)['message'])
                self.assertEqual((folder/'2026-01'/first.name).read_bytes(),full);self.assertFalse(source.exists())

    def test_repair_refuses_changed_original_or_wrong_sized_download(self):
        for failure in ('original','download'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as temporary:
                store,folder,job_id,first,full,image,source,client=self.repair_fixture(Path(temporary))
                if failure=='original':first.write_bytes(b'user replaced this file')
                else:source.write_bytes(b'truncated download')
                before=first.read_bytes();self.request_repair(store,job_id)
                with patch('organizer_repair.TelegramCLI',return_value=client):ApplyWorker(store,job_id).execute()
                self.assertEqual(self.job(store)['releases_review'],1);self.assertEqual(first.read_bytes(),before)
                self.assertFalse((folder/'2026-01'/first.name).exists())
                self.assertEqual(client.calls,0 if failure=='original' else 1)

    def test_repair_rejects_changed_preview_or_ambiguous_source_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            store,folder,job_id,*_=self.repair_fixture(Path(temporary))
            plan=json.loads(Organizer(store).path.read_text())
            plan['attachments'][0]['topic_url']='https://t.me/c/999999999/200'
            store.atomic_write(Organizer(store).path,plan)
            with self.assertRaisesRegex(ValueError,'No unambiguous'):self.request_repair(store,job_id)

    def test_stopped_repair_resumes_only_after_manual_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            store,folder,job_id,first,full,image,source,client=self.repair_fixture(Path(temporary))
            self.request_repair(store,job_id)
            def stop(*args,**kwargs):
                (store.root/'data/organizer-stop').touch();raise RuntimeError('Stopped by request')
            with patch('organizer_repair.TelegramCLI',return_value=client):
                with patch.object(client,'download',side_effect=stop):ApplyWorker(store,job_id).execute()
                self.assertEqual(self.job(store)['state'],'stopped');self.assertEqual(first.read_bytes(),b'incomplete original')
                with patch('organizer_apply.Popen'):start_application(store,{'job_id':job_id},resume=True)
                ApplyWorker(store,job_id).execute()
            self.assertEqual(client.calls,1);self.assertEqual(self.job(store)['releases_review'],0)

    def test_incomplete_volume_set_is_skipped_before_moves_even_without_images(self):
        for images in (True,False):
            with self.subTest(images=images),tempfile.TemporaryDirectory() as temporary:
                store,folder=self.setup_store(Path(temporary))
                first=folder/'Example 2026-01.7z.001';first.write_bytes(b'truncated')
                second=folder/'Example 2026-01.7z.002';second.write_bytes(b'complete second part')
                self.archive(folder/'Example 2026-02.zip')
                plan=self.preview(store,folder)
                plan['attachments'][0]['bytes_total']=2000
                match_files(plan['files'],plan['attachments'],set())
                store.atomic_write(Organizer(store).path,plan)
                job_id=self.start(store,images=images);ApplyWorker(store,job_id).execute()
                job=self.job(store)
                self.assertEqual((job['state'],job['files_done'],job['files_review']),('completed',1,1))
                self.assertEqual(job['releases_review'],1)
                self.assertIn('local: 9; Telegram: 2,000 bytes',job['groups'][0]['error'])
                self.assertEqual(first.read_bytes(),b'truncated');self.assertEqual(second.read_bytes(),b'complete second part')
                self.assertFalse((folder/'2026-01').exists())
                self.assertTrue((folder/'2026-02/Example 2026-02.zip').exists())
                self.assertFalse(store.history.was_downloaded(100));self.assertFalse(store.history.was_downloaded(101))
                self.assertTrue(store.history.was_downloaded(102))

    def test_release_failures_continue_and_retry_preserves_completed_work_and_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            store,folder=self.setup_store(Path(temporary))
            bad=folder/'Example 2026-01.zip'
            with zipfile.ZipFile(bad,'w') as z:z.writestr('../escape.jpg',b'unsafe image')
            original=bad.read_bytes()
            for month in ('02','03'):self.archive(folder/('Example 2026-'+month+'.zip'))
            self.preview(store,folder);job_id=self.start(store)
            record=store.history.import_organized
            def fail_one(job,records,*args,**kwargs):
                if records[0]['month']=='2026-02':raise OSError('temporary database failure')
                return record(job,records,*args,**kwargs)
            with patch.object(store.history,'import_organized',side_effect=fail_one):ApplyWorker(store,job_id).execute()
            job=self.job(store)
            self.assertEqual((job['files_done'],job['files_review'],job['releases_review']),(1,2,2))
            self.assertEqual(bad.read_bytes(),original);self.assertFalse((Path(temporary)/'escape.jpg').exists())
            self.assertFalse(store.history.was_downloaded(100));self.assertFalse(store.history.was_downloaded(101));self.assertTrue(store.history.was_downloaded(102))
            status=Organizer(store).status()['application']
            self.assertEqual([f['state'] for f in status['files']],['needs_review','needs_review','completed'])
            self.assertTrue(status['files'][1]['moved']);self.assertIn('database',status['files'][1]['error'])
            target=folder/'2026-03/Example 2026-03.zip';inode=target.stat().st_ino
            with patch('organizer_apply.Popen'):start_application(store,{'job_id':job_id},resume=True)
            from release_images import extract_images
            with patch('organizer_apply.extract_images',wraps=extract_images) as extraction:
                ApplyWorker(store,job_id).execute()
            self.assertEqual(extraction.call_count,1) # Only unsafe release; moved release reuses verified images.
            self.assertEqual(self.job(store)['files_done'],2);self.assertEqual(self.job(store)['files_review'],1)
            self.assertTrue(store.history.was_downloaded(101));self.assertEqual(target.stat().st_ino,inode)
            self.archive(bad);self.preview(store,folder)
            self.start(store) # A fresh preview is allowed after replacing a skipped original.

    def test_cancel_and_mount_loss_still_stop_the_whole_operation(self):
        for mode in ('stop','mount'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temporary:
                store,folder=self.setup_store(Path(temporary))
                for month in ('01','02'):self.archive(folder/('Example 2026-'+month+'.zip'))
                self.preview(store,folder);job_id=self.start(store);worker=ApplyWorker(store,job_id)
                def fail(*args,**kwargs):
                    if mode=='stop':(store.root/'data/organizer-stop').touch()
                    else:worker.job['mount']=['disconnected']
                    raise ValueError('release failure')
                with patch('organizer_apply.extract_images',side_effect=fail) as extraction:worker.execute()
                self.assertEqual(extraction.call_count,1)
                self.assertEqual(self.job(store)['state'],'stopped' if mode=='stop' else 'failed')
                self.assertEqual(self.job(store)['files_done'],0)
                self.assertEqual(len(list(folder.glob('*.zip'))),2)

    def test_part_checks_handle_gaps_and_old_rar_zip_numbering(self):
        for names,valid in [(['Release.7z.001','Release.7z.003'],False),
                            (['Release.7z.002'],False),(['Release.rar','Release.r00','Release.r01'],True),
                            (['Release.r00'],False),(['Release.zip','Release.z01','Release.z02'],True)]:
            group={'records':[{'filename':name} for name in names]}
            with self.subTest(names=names):
                if valid:check_parts(group)
                else:
                    with self.assertRaisesRegex(ValueError,'Incomplete archive set'):check_parts(group)

    def test_damaged_images_warn_while_archives_move_and_history_commits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root)
            damaged=folder/'Example 2026-01.zip';damaged_image_zip(damaged);original=damaged.read_bytes()
            self.archive(folder/'Example 2026-02.zip')
            self.preview(store,folder);job_id=self.start(store)
            ApplyWorker(store,job_id).execute()
            self.assertEqual(self.job(store)['state'],'completed',self.job(store)['message'])
            self.assertEqual(self.job(store)['files_done'],2)
            images=list((folder/'2026-01/release_images').iterdir())
            self.assertEqual({p.read_bytes() for p in images},{b'good image',b'recovered image'})
            target=folder/'2026-01'/damaged.name
            self.assertEqual(target.read_bytes(),original);self.assertFalse(damaged.exists())
            warnings=Organizer(store).status()['application']['warnings']
            self.assertTrue(any('broken.jpg' in w and 'skipped' in w for w in warnings))
            target.unlink() # Warnings and download history outlive NAS placement.
            with store.history.connect() as db:
                record=dict(db.execute('SELECT * FROM downloads WHERE filename=?',(damaged.name,)).fetchone())
            self.assertEqual(record['state'],'downloaded');self.assertEqual(record['image_count'],2)
            self.assertTrue(any('broken.jpg' in w for w in json.loads(record['image_warnings'])))

    def setup_store(self, root):
        store=SubscriptionStore(configure_source(root))
        base=root/'nas';base.mkdir();folder=base/'Example';folder.mkdir()
        store.atomic_write(store.config_path,{'revision':0,'download_directory':str(base)})
        store.atomic_write(root/'data/creators.json',{'creators':[{'name':'Example','topic_url':TOPIC,'within_approved_group':True}]})
        return store,folder

    def preview(self, store, folder):
        files=inventory(folder)
        attachments=[{'source_message_id':100+i,'message_url':TOPIC+'/'+str(100+i),
                      'filename':row['filename'],'bytes_total':row['size']} for i,row in enumerate(files)]
        match_files(files,attachments,set())
        plan={'id':'preview','state':'completed','dry_run':True,'backend':'cli','base':str(folder.parent),
              'creator':'Example','creator_folder':'Example','topic_url':TOPIC,'files':files,'attachments':attachments}
        store.atomic_write(Organizer(store).path,plan)
        return plan

    def start(self, store, images=True):
        with patch('organizer_apply.Popen'):
            result=start_application(store,{'plan_id':'preview','extract_images':images})
        return result['application']['id']

    def job(self, store):return json.loads((store.root/'data/organizer-apply.json').read_text())

    def archive(self, path):
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('one/preview.jpg',b'first image')
            z.writestr('two/preview.jpg',b'second image')
            z.writestr('model.stl',b'model data')

    def test_manual_apply_moves_matched_files_and_backfills_flat_images_and_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root)
            archive=folder/'Example 2026-01.zip';self.archive(archive);original=archive.read_bytes()
            (folder/'Loyalty.zip').write_bytes(b'unclear month')
            plan=self.preview(store,folder)
            self.assertFalse(plan['files'][1]['would_record'])
            job_id=self.start(store)
            self.assertTrue(archive.exists()) # Starting is still separate from executing.
            ApplyWorker(store,job_id).execute()
            self.assertEqual(self.job(store)['state'],'completed')
            destination=folder/'2026-01'/archive.name
            self.assertEqual(destination.read_bytes(),original);self.assertFalse(archive.exists())
            images=list((folder/'2026-01/release_images').iterdir())
            self.assertEqual({p.read_bytes() for p in images},{b'first image',b'second image'})
            self.assertTrue(all(p.is_file() for p in images));self.assertEqual(len(images),2)
            self.assertTrue((folder/'Loyalty.zip').exists())
            self.assertTrue(store.history.was_downloaded(100))
            destination.unlink() # History remains valid if archives move away later.
            self.assertTrue(store.history.was_downloaded(100))
            with store.history.connect() as db:
                record=dict(db.execute('SELECT * FROM downloads').fetchone())
                self.assertEqual(record['origin'],'organizer');self.assertIsNone(record['sha256'])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM organized_files').fetchone()[0],1)

    def test_already_organized_archive_gains_images_and_preserves_download_details(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root);month=folder/'2026-08';month.mkdir()
            archive=month/'Example 2026-08.zip';self.archive(archive)
            original=archive.stat()
            row=store.history.register(source_message_id=100,creator='Example',topic_url=TOPIC,filename=archive.name,bytes_total=archive.stat().st_size,release_month='2026-08')
            from file_delivery import digest
            store.history.complete(row['id'],destination=archive,verified_size=archive.stat().st_size,sha256=digest(archive))
            with store.history.connect() as db:before=dict(db.execute('SELECT * FROM downloads').fetchone())
            self.preview(store,folder);ApplyWorker(store,self.start(store)).execute()
            self.assertEqual(self.job(store)['state'],'completed')
            self.assertEqual(archive.stat().st_ino,original.st_ino)
            self.assertEqual(len(list((month/'release_images').iterdir())),2)
            with store.history.connect() as db:after=dict(db.execute('SELECT * FROM downloads').fetchone())
            for key in ('sha256','origin','original_destination','completed_at','download_seconds'):
                self.assertEqual(after[key],before[key])
            self.assertEqual(after['image_count'],2)

    def test_new_preview_reuses_extraction_even_after_images_move_away(self):
        for empty,warnings in ((False,[]),(True,[]),(False,['Damaged member skipped'])):
            with self.subTest(empty=empty,warnings=warnings),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);store,folder=self.setup_store(root);archive=folder/'Example 2026-01.zip'
                with zipfile.ZipFile(archive,'w') as z:
                    z.writestr('model.stl',b'model')
                    if not empty:z.writestr('preview.jpg',b'image')
                self.preview(store,folder)
                from release_images import extract_images
                def extract(*args,**kwargs):
                    result=extract_images(*args,**kwargs);kwargs['warnings'].extend(warnings);return result
                with patch('organizer_apply.extract_images',side_effect=extract):ApplyWorker(store,self.start(store)).execute()
                self.assertEqual(self.job(store)['releases_review'],0)
                destination=folder/'2026-01/release_images'
                for image in destination.glob('*'):image.unlink()
                store=SubscriptionStore(root) # Persistence across processes/jobs.
                self.preview(store,folder)
                status=Organizer(store).status()['files'][0]
                self.assertEqual(status['image_count'],0 if empty else 1)
                self.assertEqual(status['image_status'],'complete_with_warnings' if warnings else 'complete')
                self.assertTrue(status['images_extracted_at'])
                with patch('organizer_apply.extract_images',side_effect=AssertionError('must skip saved extraction')) as extraction,\
                     patch('organizer_apply.deliver',side_effect=AssertionError('must not repeat image delivery')):
                    ApplyWorker(store,self.start(store)).execute()
                extraction.assert_not_called()
                self.assertEqual(self.job(store)['releases_review'],0,self.job(store)['message'])
                self.assertTrue(self.job(store)['groups'][0]['images_reused'])
                self.assertEqual(self.job(store)['groups'][0]['image_warnings'],warnings)
                self.assertEqual(list(destination.glob('*')),[])

    def test_images_saved_before_archive_move_survive_a_new_job(self):
        for failure in ('images','archives'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);store,folder=self.setup_store(root);archive=folder/'Example 2026-01.zip';self.archive(archive)
                self.preview(store,folder);job_id=self.start(store)
                target='organizer_apply.deliver' if failure=='images' else 'organizer_apply.PinnedFolder.move'
                with patch(target,side_effect=OSError('test interrupted move')):ApplyWorker(store,job_id).execute()
                self.assertEqual(self.job(store)['releases_review'],1);self.assertFalse(store.history.was_downloaded(100))
                records=self.job(store)['groups'][0]['records'];destination=folder/'2026-01/release_images'
                saved=store.history.image_extraction(TOPIC,records,destination)
                self.assertEqual(saved is not None,failure=='archives')
                self.preview(store,folder)
                from release_images import extract_images
                with patch('organizer_apply.extract_images',wraps=extract_images) as extraction:
                    ApplyWorker(store,self.start(store)).execute()
                self.assertEqual(extraction.call_count,1 if failure=='images' else 0)
                self.assertEqual(self.job(store)['releases_review'],0,self.job(store)['message'])
                self.assertTrue(store.history.was_downloaded(100))

    def test_extraction_receipt_requires_the_same_source_parts_sizes_and_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root);destination=folder/'2026-01/release_images'
            first={'filename':'Example 2026-01.7z.001','size':100};second={'filename':'Example 2026-01.7z.002','size':20}
            history=store.history
            history.record_image_extraction(TOPIC,[first,second],[],destination)
            self.assertIsNotNone(history.image_extraction(TOPIC,[second,first],destination))
            self.assertIsNone(history.image_extraction(TOPIC,[first],destination))
            self.assertIsNone(history.image_extraction(TOPIC,[first,dict(second,size=21)],destination))
            self.assertIsNone(history.image_extraction(TOPIC+'0',[first,second],destination))
            self.assertIsNone(history.image_extraction(TOPIC,[first,second],folder/'2026-02/release_images'))
            with self.assertRaises(ValueError):history.image_extraction('https://t.me/c/987654321/200',[first],destination)

    def test_legacy_manifests_are_migrated_and_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root);archive=folder/'Example 2026-01.zip';self.archive(archive)
            self.preview(store,folder);ApplyWorker(store,self.start(store)).execute()
            with store.history.connect() as db:
                db.execute('DELETE FROM image_extractions')
                db.execute('ALTER TABLE downloads DROP COLUMN image_status')
                db.execute('ALTER TABLE downloads DROP COLUMN images_extracted_at')
            store=SubscriptionStore(root);self.preview(store,folder)
            self.assertEqual(Organizer(store).status()['files'][0]['image_status'],'complete')
            with patch('organizer_apply.extract_images',side_effect=AssertionError('reuse legacy manifest')) as extraction:
                ApplyWorker(store,self.start(store)).execute()
            extraction.assert_not_called();self.assertEqual(self.job(store)['releases_review'],0)

    def test_changed_source_and_new_destination_skip_before_mutation_or_history(self):
        for changed in ('size','same_size','destination','symlink'):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);store,folder=self.setup_store(root);source=folder/'Example 2026-01.zip'
                source.write_bytes(b'original');self.archive(folder/'Example 2026-02.zip');self.preview(store,folder)
                if changed=='size':source.write_bytes(b'changed')
                if changed=='same_size':source.write_bytes(b'changed!');os.utime(source,ns=(1,1))
                if changed=='destination':
                    (folder/'2026-01').mkdir();(folder/'2026-01'/source.name).write_bytes(b'existing')
                if changed=='symlink':
                    source.unlink();source.symlink_to(root/'outside')
                ApplyWorker(store,self.start(store)).execute()
                self.assertEqual(self.job(store)['state'],'completed')
                self.assertEqual(self.job(store)['releases_review'],1)
                self.assertFalse(store.history.was_downloaded(100))
                self.assertTrue(store.history.was_downloaded(101))

    def test_resume_after_rename_and_failed_history_commit_uses_saved_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root);source=folder/'Example 2026-01.zip';self.archive(source)
            self.preview(store,folder);job_id=self.start(store)
            with patch.object(store.history,'import_organized',side_effect=OSError('database unavailable')):
                ApplyWorker(store,job_id).execute()
            self.assertEqual(self.job(store)['state'],'completed');self.assertGreater(self.job(store)['releases_review'],0);self.assertFalse(store.history.was_downloaded(100))
            self.assertFalse(source.exists());self.assertTrue((folder/'2026-01'/source.name).exists())
            status=Organizer(store).status()['application']
            self.assertNotIn('groups',status);self.assertNotIn('mount',status)
            self.assertTrue(status['files'][0]['moved']);self.assertFalse(status['files'][0]['recorded'])
            self.assertEqual(status['files'][0]['state'],'needs_review')
            with patch('organizer_apply.extract_images',side_effect=AssertionError('must reuse saved extraction')):
                ApplyWorker(store,job_id).execute()
            self.assertEqual(self.job(store)['state'],'completed');self.assertTrue(store.history.was_downloaded(100))
            status=Organizer(store).status()['application']
            self.assertTrue(status['files'][0]['recorded']);self.assertEqual(status['files'][0]['state'],'completed')
            self.assertEqual(len(list((folder/'2026-01/release_images').iterdir())),2)

    def test_live_status_tracks_each_file_without_claiming_history_before_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root)
            for month in ('01','02'):(folder/('Example 2026-'+month+'.zip')).write_bytes(b'archive')
            self.preview(store,folder);job_id=self.start(store,images=False)
            snapshots=[];original=store.history.import_organized
            def record(*args,**kwargs):
                snapshots.append(Organizer(store).status()['application'])
                return original(*args,**kwargs)
            with patch.object(store.history,'import_organized',side_effect=record):ApplyWorker(store,job_id).execute()
            self.assertEqual(len(snapshots),2)
            first,second=snapshots
            self.assertTrue(first['files'][0]['moved']);self.assertFalse(first['files'][0]['recorded'])
            self.assertEqual(first['files'][0]['state'],'recording');self.assertEqual(first['files'][1]['state'],'pending')
            self.assertTrue(second['files'][0]['recorded']);self.assertFalse(second['files'][1]['recorded'])
            self.assertTrue(all(f['recorded'] for f in Organizer(store).status()['application']['files']))

    def test_split_set_spanning_flat_and_month_folders_extracts_and_commits_together(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root);image=root/'preview.jpg';image.write_bytes(os.urandom(6000))
            subprocess.run(['7z','a','-mx0','-v4k',str(folder/'Example 2026-07.7z'),str(image)],stdout=subprocess.DEVNULL,check=True)
            parts=sorted(folder.glob('*.7z.*'));self.assertEqual(len(parts),2)
            (folder/'2026-07').mkdir();parts[1].rename(folder/'2026-07'/parts[1].name)
            self.preview(store,folder);ApplyWorker(store,self.start(store)).execute()
            self.assertEqual(self.job(store)['state'],'completed')
            self.assertTrue(all((folder/'2026-07'/p.name).exists() for p in parts))
            self.assertEqual(list((folder/'2026-07/release_images').iterdir())[0].read_bytes(),image.read_bytes())
            self.assertTrue(store.history.was_downloaded(100));self.assertTrue(store.history.was_downloaded(101))

    def test_cancel_preserves_originals_and_can_resume_without_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root);source=folder/'Example 2026-01.zip';self.archive(source)
            self.preview(store,folder);job_id=self.start(store,images=False)
            (root/'data/organizer-stop').touch();ApplyWorker(store,job_id).execute()
            self.assertEqual(self.job(store)['state'],'stopped');self.assertTrue(source.exists())
            with patch('organizer_apply.Popen'):
                result=start_application(store,{'job_id':job_id},resume=True)
            self.assertEqual(result['application']['id'],job_id)
            ApplyWorker(store,job_id).execute()
            self.assertEqual(self.job(store)['state'],'completed')
            self.assertFalse((folder/'2026-01/release_images').exists())

    def test_unsafe_destinations_and_conflicts_are_never_applied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root);source=folder/'Example 2026-01.zip';source.write_bytes(b'original')
            month=folder/'2026-01';month.mkdir();(month/source.name).write_bytes(b'original')
            plan=self.preview(store,folder);groups=ready_groups(plan)
            self.assertEqual(len(groups),1);self.assertEqual(len(groups[0]['records']),1)
            self.assertEqual(groups[0]['records'][0]['action'],'keep')
            plan['files'][0]['destination']='../outside.zip'
            with self.assertRaises(ValueError):ready_groups(plan)
            pinned=PinnedFolder(folder.parent,'Example')
            try:
                with self.assertRaises(ValueError):pinned.parent('../outside.zip')
            finally:pinned.close()

    def test_active_download_allows_apply_but_replaced_preview_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root);self.archive(folder/'Example 2026-01.zip');self.preview(store,folder)
            with patch('organizer_apply.Popen') as launch:
                with self.assertRaises(ValueError):start_application(store,{'plan_id':'old','extract_images':True})
                with patch.object(store,'queue',return_value={'active':True}):
                    start_application(store,{'plan_id':'preview','extract_images':True})
                launch.assert_called_once()
                with self.assertRaises(FileExistsError):start_application(store,{'plan_id':'preview','extract_images':True})

    def test_preview_can_start_during_download_and_rejects_second_preview(self):
        import fcntl
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root)
            store.atomic_write(store.runs.path,{'state':'downloading'})
            with (root/'data/worker.lock').open('a') as lock,patch('folder_organizer.subprocess') as process_module:
                launch=process_module.Popen
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                payload={'dry_run':True,'topic_url':TOPIC,'creator_folder':'Example'}
                self.assertEqual(Organizer(store).start(payload)['state'],'starting')
                launch.assert_called_once()
                with self.assertRaises(FileExistsError):Organizer(store).start(payload)

    def test_downloaded_destination_reuse_survives_failed_history_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root);source=folder/'Example 2026-01.zip';self.archive(source)
            self.preview(store,folder);job_id=self.start(store,images=False)
            target=folder/'2026-01'/source.name;target.parent.mkdir();target.write_bytes(source.read_bytes())
            original=target.read_bytes();inode=target.stat().st_ino
            with patch.object(store.history,'import_organized',side_effect=OSError('test database failure')):
                ApplyWorker(store,job_id).execute()
            self.assertEqual(self.job(store)['state'],'completed');self.assertGreater(self.job(store)['releases_review'],0);self.assertFalse(source.exists())
            self.assertFalse(store.history.was_downloaded(100))
            with patch('organizer_apply.Popen'):start_application(store,{'job_id':job_id},resume=True)
            ApplyWorker(store,job_id).execute()
            self.assertEqual(self.job(store)['state'],'completed');self.assertTrue(store.history.was_downloaded(100))
            self.assertEqual(target.read_bytes(),original);self.assertEqual(target.stat().st_ino,inode)

    def test_different_destination_arriving_after_preview_preserves_both_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root);source=folder/'Example 2026-01.zip';source.write_bytes(b'original')
            self.preview(store,folder);job_id=self.start(store,images=False)
            target=folder/'2026-01'/source.name;target.parent.mkdir();target.write_bytes(b'conflict')
            ApplyWorker(store,job_id).execute()
            self.assertEqual(self.job(store)['state'],'completed');self.assertGreater(self.job(store)['releases_review'],0)
            self.assertEqual(source.read_bytes(),b'original');self.assertEqual(target.read_bytes(),b'conflict')
            self.assertFalse(store.history.was_downloaded(100))

    def legacy_parts_job(self, root):
        store,folder=self.setup_store(root)
        parts=['Example 2026-01_part1.rar','Example 2026-01_part2.rar']
        for i,name in enumerate(parts):(folder/name).write_bytes(('archive part '+str(i+1)).encode())
        (folder/'Example 2026-02.zip').write_bytes(b'another release')
        plan=self.preview(store,folder);job_id=self.start(store)
        job=self.job(store)
        # Simulate the old journal: first part completed, second not processed,
        # and an unrelated release with cached extraction at its original index.
        job['groups']=[{'key':r['month']+'/'+r['filename'].casefold(),'month':r['month'],
                        'records':[r],'state':'pending'} for r in plan['files']]
        first=job['groups'][0];record=first['records'][0]
        pinned=PinnedFolder(folder.parent,'Example')
        try:
            record['move_started']=True;pinned.move(record);record['moved']=True
        finally:pinned.close()
        images_folder=folder/'2026-01/release_images';images_folder.mkdir()
        image=images_folder/flat_image_name(parts[0],'gallery/preview.jpg');image.write_bytes(b'original image')
        first['images']=[{'path':image.name,'source_path':'gallery/preview.jpg','size':image.stat().st_size,'sha256':digest(image)}]
        store.history.import_organized(job,first['records'],first['images'],images_folder)
        first['state']='completed'
        cached=store.root/'data/organizer-work'/job_id/'2/images/output/other.jpg'
        cached.parent.mkdir(parents=True);cached.write_bytes(b'cached image')
        job['groups'][2]['images']=[{'path':flat_image_name('Example 2026-02.zip','other.jpg'),
                                   'source_path':'other.jpg','size':cached.stat().st_size,'sha256':digest(cached)}]
        job.update(state='failed',files_done=1,moves_done=1,releases_total=3,releases_done=1,
                   current_release=job['groups'][1]['key'],phase='extracting')
        store.atomic_write(store.root/'data/organizer-apply.json',job)
        return store,folder,job_id,parts,image

    def test_legacy_parts_resume_preserves_counts_images_history_and_cached_work(self):
        with tempfile.TemporaryDirectory() as temp:
            store,folder,job_id,parts,image=self.legacy_parts_job(Path(temp))
            first_inode=(folder/'2026-01'/parts[0]).stat().st_ino;image_inode=image.stat().st_ino
            with store.history.connect() as db:before=dict(db.execute('SELECT * FROM downloads').fetchone())
            old_job=self.job(store)
            with patch('organizer_apply.Popen'):
                status=start_application(store,{'job_id':job_id},resume=True)['application']
            self.assertEqual(status['files_done'],1);self.assertTrue(status['files'][0]['recorded'])
            job=self.job(store)
            self.assertEqual((job['releases_total'],job['releases_done']),(2,0))
            self.assertEqual([g['work_index'] for g in job['groups']],[1,2])
            self.assertEqual(len(job['groups'][0]['records']),2)
            backup=store.root/'data/organizer-jobs'/(job_id+'.before-volume-grouping.json')
            self.assertEqual(json.loads(backup.read_text()),old_job)
            def extract(first,work,progress,stopped,**kwargs):
                self.assertEqual(first.name,parts[0])
                self.assertEqual({p.name for p in first.parent.iterdir()},set(parts))
                for i,name in enumerate(parts):self.assertEqual((first.parent/name).read_bytes(),('archive part '+str(i+1)).encode())
                target=work/'output/gallery/preview.jpg';target.parent.mkdir(parents=True);target.write_bytes(b'original image')
                return [target]
            with patch('organizer_apply.extract_images',side_effect=extract) as extraction,patch.object(store.history,'import_organized',side_effect=OSError('temporary history error')):
                ApplyWorker(store,job_id).execute()
            self.assertEqual(extraction.call_count,1)
            failed=self.job(store)
            self.assertEqual((failed['state'],failed['files_done'],failed['moves_done']),('completed',1,3))
            self.assertTrue(Organizer(store).status()['application']['files'][0]['recorded'])
            self.assertFalse(Organizer(store).status()['application']['files'][1]['recorded'])
            with patch('organizer_apply.Popen'):
                start_application(store,{'job_id':job_id},resume=True)
            self.assertEqual([g.get('images') for g in self.job(store)['groups']],[g.get('images') for g in failed['groups']]) # Retry preserves cached images.
            self.assertEqual([g.get('work_index') for g in self.job(store)['groups']],[g.get('work_index') for g in failed['groups']])
            with patch('organizer_apply.extract_images',side_effect=AssertionError('must reuse both cached extractions')):
                ApplyWorker(store,job_id).execute()
            done=self.job(store)
            self.assertEqual((done['state'],done['files_done'],done['moves_done'],done['releases_done']),('completed',3,3,2))
            self.assertTrue(all(f['recorded'] for f in Organizer(store).status()['application']['files']))
            self.assertEqual((folder/'2026-01'/parts[0]).stat().st_ino,first_inode)
            self.assertEqual(image.stat().st_ino,image_inode)
            self.assertEqual(len(list(image.parent.iterdir())),1)
            with store.history.connect() as db:
                self.assertEqual(dict(db.execute('SELECT * FROM downloads WHERE id=?',(before['id'],)).fetchone()),before)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM downloads').fetchone()[0],3)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM organized_files').fetchone()[0],3)

    def test_resumed_group_rechecks_completed_part_before_touching_pending_files(self):
        for change in ('missing','changed'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                store,folder,job_id,parts,image=self.legacy_parts_job(Path(temp))
                first=folder/'2026-01'/parts[0]
                if change=='missing':first.unlink()
                else:first.write_bytes(b'changed')
                with patch('organizer_apply.Popen'):
                    start_application(store,{'job_id':job_id},resume=True)
                with patch('organizer_apply.extract_images') as extraction:ApplyWorker(store,job_id).execute()
                extraction.assert_not_called()
                self.assertEqual(self.job(store)['state'],'completed');self.assertGreater(self.job(store)['releases_review'],0)
                self.assertTrue((folder/parts[1]).is_file());self.assertFalse((folder/'2026-01'/parts[1]).exists())
                with store.history.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM downloads').fetchone()[0],2)

    def test_fresh_preview_groups_underscore_parts_and_rejects_competing_part_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            store,folder=self.setup_store(Path(temp))
            for part in ('_part1','_part2'):(folder/('Example 2026-01'+part+'.rar')).write_bytes(b'part')
            plan=self.preview(store,folder)
            self.assertEqual(len(ready_groups(plan)),1)
            (folder/'Example 2026-01.part1.rar').write_bytes(b'competing part')
            plan=self.preview(store,folder)
            with self.assertRaisesRegex(ValueError,'competing archive parts'):ready_groups(plan)


if __name__=='__main__':unittest.main()
