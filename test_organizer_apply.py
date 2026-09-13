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
from organizer_apply import ApplyWorker, PinnedFolder, ready_groups, start_application
from release_images import flat_image_name
from file_delivery import digest
from subscription_store import SubscriptionStore

TOPIC='https://t.me/c/123456789/200'


class OrganizerApplyTests(unittest.TestCase):
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

    def test_changed_source_and_new_destination_reject_before_launch_or_history(self):
        for changed in ('size','same_size','destination','symlink'):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);store,folder=self.setup_store(root);source=folder/'Example 2026-01.zip'
                source.write_bytes(b'original');self.preview(store,folder)
                if changed=='size':source.write_bytes(b'changed')
                if changed=='same_size':source.write_bytes(b'changed!');os.utime(source,ns=(1,1))
                if changed=='destination':
                    (folder/'2026-01').mkdir();(folder/'2026-01'/source.name).write_bytes(b'existing')
                if changed=='symlink':
                    source.unlink();source.symlink_to(root/'outside')
                with patch('organizer_apply.Popen') as launch:
                    with self.assertRaises((OSError,ValueError)):start_application(store,{'plan_id':'preview','extract_images':True})
                    launch.assert_not_called()
                self.assertFalse(store.history.was_downloaded(100))

    def test_resume_after_rename_and_failed_history_commit_uses_saved_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root);source=folder/'Example 2026-01.zip';self.archive(source)
            self.preview(store,folder);job_id=self.start(store)
            with patch.object(store.history,'import_organized',side_effect=OSError('database unavailable')):
                ApplyWorker(store,job_id).execute()
            self.assertEqual(self.job(store)['state'],'failed');self.assertFalse(store.history.was_downloaded(100))
            self.assertFalse(source.exists());self.assertTrue((folder/'2026-01'/source.name).exists())
            status=Organizer(store).status()['application']
            self.assertNotIn('groups',status);self.assertNotIn('mount',status)
            self.assertTrue(status['files'][0]['moved']);self.assertFalse(status['files'][0]['recorded'])
            self.assertEqual(status['files'][0]['state'],'paused')
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

    def test_active_download_and_replaced_preview_block_apply(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.setup_store(root);self.archive(folder/'Example 2026-01.zip');self.preview(store,folder)
            with patch('organizer_apply.Popen') as launch:
                with self.assertRaises(ValueError):start_application(store,{'plan_id':'old','extract_images':True})
                with patch.object(store,'queue',return_value={'active':True}):
                    with self.assertRaises(FileExistsError):start_application(store,{'plan_id':'preview','extract_images':True})
                launch.assert_not_called()

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
            def extract(first,work,progress,stopped):
                self.assertEqual(first.name,parts[0])
                self.assertEqual({p.name for p in first.parent.iterdir()},set(parts))
                for i,name in enumerate(parts):self.assertEqual((first.parent/name).read_bytes(),('archive part '+str(i+1)).encode())
                target=work/'output/gallery/preview.jpg';target.parent.mkdir(parents=True);target.write_bytes(b'original image')
                return [target]
            with patch('organizer_apply.extract_images',side_effect=extract) as extraction,patch.object(store.history,'import_organized',side_effect=OSError('temporary history error')):
                ApplyWorker(store,job_id).execute()
            self.assertEqual(extraction.call_count,1)
            failed=self.job(store)
            self.assertEqual((failed['state'],failed['files_done'],failed['moves_done']),('failed',1,2))
            self.assertTrue(Organizer(store).status()['application']['files'][0]['recorded'])
            self.assertFalse(Organizer(store).status()['application']['files'][1]['recorded'])
            with patch('organizer_apply.Popen'):
                start_application(store,{'job_id':job_id},resume=True)
            self.assertEqual(self.job(store)['groups'],failed['groups']) # Migration is idempotent.
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
                self.assertEqual(self.job(store)['state'],'failed')
                self.assertTrue((folder/parts[1]).is_file());self.assertFalse((folder/'2026-01'/parts[1]).exists())
                with store.history.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM downloads').fetchone()[0],1)

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
