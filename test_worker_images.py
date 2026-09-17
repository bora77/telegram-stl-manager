from test_support import configure_source
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import threading
from concurrent.futures import ThreadPoolExecutor

import download_worker
from file_delivery import digest
from release_images import ExtractionError, MissingVolumeError, extract_images
from folder_organizer import Organizer, inventory, match_files
from organizer_apply import ApplyWorker, start_application
from subscription_store import SubscriptionStore


class WorkerImageTests(unittest.TestCase):
    def test_download_reuses_artist_prefixed_release_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            worker,store,sub,state,base=self.setup_worker(Path(temp))
            release=base/'Example'/'Example 2026-09';release.mkdir(parents=True)
            source=state/'archive.zip';source.write_bytes(b'archive')
            result=worker.move_one(source,sub,'2026-09','archive.zip')
            self.assertEqual(result['destination'],str(release/'archive.zip'))
            self.assertFalse((base/'Example'/'2026-09').exists())


    def test_repeated_split_upload_finishes_once_using_selected_message_ids(self):
        from download_plan import DownloadPlan
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);worker,store,sub,state,base=self.setup_worker(root)
            image=root/'preview.jpg';image.write_bytes(os.urandom(9000))
            subprocess.run(['7z','a','-mx0','-v4k',str(root/'Example 2026-09.7z'),str(image)],stdout=subprocess.DEVNULL,check=True)
            parts=sorted(root.glob('Example 2026-09.7z.*'));entries=[]
            for offset in (0,100):
                for index,part in enumerate(parts,1):
                    mid=offset+index;item={'filename':part.name,'source_message_id':mid,'bytes_total':part.stat().st_size,'topic_url':sub['topic_url'],'message_url':sub['topic_url']+'/'+str(mid)}
                    entries.append({'item':item,'month':'2026-09'})
                    store.history.register(source_message_id=mid,creator=sub['creator'],topic_url=sub['topic_url'],filename=part.name,bytes_total=part.stat().st_size,batch_id=worker.batch)
            DownloadPlan(store,worker.run).save(sub,entries,{'creator':sub['creator']},[])
            calls=[]
            class CLI:
                def list_files(self,*args,**kwargs):raise AssertionError('Use the saved scan')
                def download(self,item,progress,*args):
                    calls.append(item['source_message_id']);path=state/str(item['source_message_id']);path.write_bytes((root/item['filename']).read_bytes());progress(path.stat().st_size,path.stat().st_size);return path
                def close(self):pass
            with patch.object(download_worker,'TelegramCLI',return_value=CLI()):worker.execute()
            self.assertEqual(worker.run['state'],'completed',worker.run['message'])
            self.assertEqual(calls,list(range(101,101+len(parts))))
            self.assertFalse(any(store.history.was_downloaded(i) for i in range(1,1+len(parts))))
            self.assertEqual([p.read_bytes() for p in (base/'Example/2026-09/release_images').iterdir()],[image.read_bytes()])
            self.assertEqual(store.history.batch_outcome(worker.batch)['unfinished_files'],0)

    def test_review_resume_reuses_verified_files_and_clears_previous_attempt_warnings(self):
        from download_plan import DownloadPlan
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);worker,store,sub,state,base=self.setup_worker(root)
            store.atomic_write(store.config_path,{'revision':0,'download_directory':str(base)})
            fixture=state/'fixture.zip'
            with zipfile.ZipFile(fixture,'w') as z:z.writestr('preview.jpg',b'image')
            item={'filename':'Example 2026-09.zip','source_message_id':1,'topic_url':sub['topic_url'],
                  'message_url':sub['topic_url']+'/1','bytes_total':fixture.stat().st_size}
            class CLI:
                def download(self,*args):raise AssertionError('Verified download must be reused')
                def list_files(self,*args,**kwargs):raise AssertionError('Completed artist scan must be reused')
                def close(self):pass
            worker.telegram=CLI()
            with patch.object(worker,'move_one',side_effect=OSError('old delivery error')):
                with self.assertRaises(OSError):worker.transfer(sub,item,'2026-09',downloaded=fixture)
            worker.run.update(state='needs_review',plan_version=1,warnings=['Old unrelated problem'],resume_requested=True)
            store.atomic_write(worker.path,worker.run)
            DownloadPlan(store,worker.run).save(sub,[{'item':item,'month':'2026-09'}],{'creator':sub['creator']},['Old scan warning'])
            self.assertTrue(store.queue()['can_resume'])
            self.assertNotIn('Old unrelated problem',store.queue()['run']['warnings'])
            with patch('run_manager.subprocess.Popen'):store.runs.resume({'run_id':worker.batch})
            with patch.object(download_worker,'TelegramCLI',return_value=CLI()):
                resumed=download_worker.Worker(worker.batch);resumed.execute()
            self.assertEqual(resumed.run['state'],'completed',resumed.run['message'])
            self.assertEqual(resumed.run['warnings'],[]);self.assertTrue(store.history.was_downloaded(1))
            self.assertFalse(store.queue()['can_resume']);self.assertEqual(store.queue()['run']['warnings'],[])
            self.assertTrue((base/'Example/2026-09'/item['filename']).is_file())

    def test_duplicate_upload_selection_keeps_one_whole_set_and_retains_old_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);worker,store,sub,state,base=self.setup_worker(root);entries=[]
            for mid,size,part in ((1,100,1),(2,20,2),(3,100,1),(4,20,2),(5,90,1),(6,10,2)):
                item={'filename':f'Example 2026-09.7z.{part:03}','bytes_total':size,'source_message_id':mid,'topic_url':sub['topic_url'],'message_url':sub['topic_url']+'/'+str(mid)}
                entries.append({'item':item,'month':'2026-09'})
                row=store.history.register(source_message_id=mid,creator=sub['creator'],topic_url=sub['topic_url'],filename=item['filename'],bytes_total=size,batch_id=worker.batch)
                folder=state/'staging'/str(row['id']);folder.mkdir(parents=True);(folder/item['filename']).write_bytes(b'kept')
            before={str(p):p.read_bytes() for p in (state/'staging').rglob('*') if p.is_file()}
            selected=worker.select_uploads(sub,entries)
            self.assertEqual([r['item']['source_message_id'] for r in selected],[3,4])
            self.assertEqual(worker.archive_versions,{3:'4',4:'4'})
            self.assertEqual(store.history.queue(worker.batch)['total_files'],2)
            self.assertEqual(before,{str(p):p.read_bytes() for p in (state/'staging').rglob('*') if p.is_file()})
            self.assertFalse(any(store.history.was_downloaded(mid) for mid in range(1,7)))

    def setup_worker(self,root):
        state=root/'telegram';state.mkdir();base=root/'destination';base.mkdir()
        store=SubscriptionStore(configure_source(root))
        topic='https://t.me/c/123456789/200'
        sub={'creator':'Example','topic_url':topic,'creator_folder':'Example','download_scope':'all_and_future'}
        store.atomic_write(root/'data/run.json',{'id':'test','state':'starting','subscriptions':[sub],'download_directory':str(base),'warnings':[],'creators_done':0})
        self.enterContext(patch.object(download_worker,'ROOT',root))
        self.enterContext(patch.object(download_worker,'STATE',state))
        worker=download_worker.Worker('test')
        return worker,store,sub,state,base

    def parallel_workers(self,root,download_month):
        worker,store,sub,state,base=self.setup_worker(root)
        folder=base/sub['creator_folder'];folder.mkdir()
        archive=folder/'Example 2026-09.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('preview.jpg',b'image');z.writestr('model.stl',b'model')
        original=archive.read_bytes()
        store.atomic_write(store.config_path,{'revision':0,'download_directory':str(base)})
        store.atomic_write(root/'data/creators.json',{'creators':[{'name':sub['creator'],'topic_url':sub['topic_url'],'within_approved_group':True}]})
        attachment={'source_message_id':100,'filename':archive.name,'bytes_total':len(original),'message_url':sub['topic_url']+'/100'}
        files=inventory(folder);match_files(files,[attachment],set())
        plan={'id':'preview','state':'completed','dry_run':True,'backend':'cli','base':str(base),**sub,'files':files,'attachments':[attachment]}
        store.atomic_write(Organizer(store).path,plan)
        with patch('organizer_apply.Popen'):
            job=start_application(store,{'plan_id':'preview','extract_images':True})['application']
        organizer=ApplyWorker(store,job['id'])
        item={**attachment,'filename':'Example '+download_month+'.zip',
              'source_message_id':100 if download_month=='2026-09' else 200,
              'message_url':sub['topic_url']+('/100' if download_month=='2026-09' else '/200')}
        return worker,organizer,store,state,folder,item,original

    def test_organizer_and_download_work_simultaneously_on_different_months(self):
        with tempfile.TemporaryDirectory() as temp:
            worker,organizer,store,state,folder,item,original=self.parallel_workers(Path(temp),'2026-10')
            extracting=threading.Event();release=threading.Event();calls=[]
            def held_extraction(*args,**kwargs):
                extracting.set()
                if not release.wait(10):raise RuntimeError('Organizer test timed out')
                return extract_images(*args,**kwargs)
            class CLI:
                def __init__(self,**kwargs):pass
                def list_files(self,*args,**kwargs):return [item]
                def download(self,item,progress,*args):
                    calls.append(item['source_message_id']);source=state/item['filename'];source.write_bytes(original)
                    progress(len(original),len(original));return source
                def close(self):pass
            with patch('organizer_apply.extract_images',side_effect=held_extraction),patch.object(download_worker,'TelegramCLI',CLI),ThreadPoolExecutor(2) as pool:
                applying=pool.submit(organizer.execute)
                try:
                    self.assertTrue(extracting.wait(5))
                    downloading=pool.submit(worker.execute);downloading.result(timeout=5)
                    self.assertFalse(applying.done())
                    self.assertEqual(worker.run['state'],'completed',worker.run['message'])
                    self.assertTrue(store.history.was_downloaded(200))
                    self.assertFalse(store.history.was_downloaded(100))
                finally:release.set()
                applying.result(timeout=5)
            self.assertEqual(organizer.job['state'],'completed',organizer.job['message'])
            self.assertEqual(calls,[200]);self.assertTrue(store.history.was_downloaded(100))

    def test_same_month_rechecks_history_when_organizer_finishes_first(self):
        with tempfile.TemporaryDirectory() as temp:
            worker,organizer,store,state,folder,item,original=self.parallel_workers(Path(temp),'2026-09')
            extracting=threading.Event();release=threading.Event();waiting=threading.Event();calls=[]
            def held_extraction(*args,**kwargs):
                extracting.set()
                if not release.wait(10):raise RuntimeError('Organizer test timed out')
                return extract_images(*args,**kwargs)
            update=worker.update
            def observed_update(state,message,**extra):
                if message.startswith('Waiting for organization'):waiting.set()
                update(state,message,**extra)
            class CLI:
                def __init__(self,**kwargs):pass
                def list_files(self,*args,**kwargs):return [item]
                def download(self,*args):calls.append(True);raise AssertionError('Organizer already supplied the file')
                def close(self):pass
            with patch('organizer_apply.extract_images',side_effect=held_extraction),patch.object(download_worker,'TelegramCLI',CLI),patch.object(worker,'update',side_effect=observed_update),ThreadPoolExecutor(2) as pool:
                applying=pool.submit(organizer.execute)
                try:
                    self.assertTrue(extracting.wait(5));downloading=pool.submit(worker.execute)
                    self.assertTrue(waiting.wait(5));self.assertFalse(downloading.done())
                finally:release.set()
                applying.result(timeout=5);downloading.result(timeout=5)
            self.assertEqual(worker.run['state'],'completed',worker.run['message'])
            self.assertEqual(organizer.job['state'],'completed',organizer.job['message'])
            self.assertEqual(calls,[]);self.assertTrue(store.history.was_downloaded(100))
            self.assertEqual((folder/'2026-09'/item['filename']).read_bytes(),original)

    def test_same_month_reuses_identical_destination_when_download_finishes_first(self):
        with tempfile.TemporaryDirectory() as temp:
            worker,organizer,store,state,folder,item,original=self.parallel_workers(Path(temp),'2026-09')
            transferring=threading.Event();release=threading.Event();waiting=threading.Event()
            progress=organizer.progress
            def observed_progress(phase,*args,**kwargs):
                if phase=='waiting':waiting.set()
                progress(phase,*args,**kwargs)
            class CLI:
                def __init__(self,**kwargs):pass
                def list_files(self,*args,**kwargs):return [item]
                def download(self,item,progress,*args):
                    transferring.set()
                    if not release.wait(10):raise RuntimeError('Download test timed out')
                    source=state/item['filename'];source.write_bytes(original);progress(len(original),len(original));return source
                def close(self):pass
            with patch.object(download_worker,'TelegramCLI',CLI),patch.object(organizer,'progress',side_effect=observed_progress),\
                 patch('organizer_apply.extract_images',side_effect=AssertionError('download already extracted images')),ThreadPoolExecutor(2) as pool:
                downloading=pool.submit(worker.execute)
                try:
                    self.assertTrue(transferring.wait(5));applying=pool.submit(organizer.execute)
                    self.assertTrue(waiting.wait(5));self.assertFalse(applying.done())
                finally:release.set()
                downloading.result(timeout=5);applying.result(timeout=5)
            self.assertEqual(worker.run['state'],'completed',worker.run['message'])
            self.assertEqual(organizer.job['state'],'completed',organizer.job['message'])
            self.assertFalse((folder/item['filename']).exists())
            self.assertEqual((folder/'2026-09'/item['filename']).read_bytes(),original)
            self.assertEqual(len(list((folder/'2026-09/release_images').iterdir())),1)
            with store.history.connect() as db:
                record=dict(db.execute('SELECT * FROM downloads WHERE source_message_id=100').fetchone())
            self.assertEqual(record['state'],'downloaded');self.assertEqual(record['sha256'],digest(folder/'2026-09'/item['filename']))

    def test_separately_posted_numbered_rars_reuse_verified_first_part_on_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);worker,store,sub,state,base=self.setup_worker(root)
            names=['Example 2026-09 1.rar','Example 2026-09 2.rar']
            contents=[b'first archive part',b'second archive part']
            items=[{'filename':name,'source_message_id':message,'message_url':sub['topic_url']+'/'+str(message),
                    'bytes_total':len(content)} for name,message,content in zip(names,[101,307],contents)]
            first=store.history.register(source_message_id=101,creator='Example',topic_url=sub['topic_url'],
                filename=names[0],bytes_total=len(contents[0]),release_month='2026-09',batch_id='previous')
            staged=state/'staging'/str(first['id']);staged.mkdir(parents=True)
            (staged/names[0]).write_bytes(contents[0])
            store.atomic_write(staged/'receipt.json',{'message_url':items[0]['message_url'],
                'size':len(contents[0]),'sha256':digest(staged/names[0])})
            metrics={'download_started_at':'2026-09-01T12:00:00+00:00','download_finished_at':'2026-09-01T12:00:02+00:00',
                     'download_seconds':2.0,'download_speed_bps':9.0,'download_average_bps':9.0}
            store.history.record_transfer(first['id'],len(contents[0]),len(contents[0]),metrics)
            with store.history.connect() as db:db.execute("UPDATE downloads SET state='paused' WHERE id=?",(first['id'],))
            calls=[];extracted=[]
            class CLI:
                def __init__(self,**kwargs):pass
                def list_files(self,*args,**kwargs):return list(reversed(items))
                def download(self,item,progress,stopped,status=None):
                    calls.append(item['source_message_id'])
                    source=state/item['filename'];source.write_bytes(contents[1])
                    progress(source.stat().st_size,source.stat().st_size);return source
                def close(self):pass
            def extract(source,work,*args,**kwargs):
                extracted.append(sorted(p.name for p in source.parent.iterdir()))
                if not (source.parent/names[1]).exists():raise MissingVolumeError(source,names[1])
                self.assertEqual([ (source.parent/name).read_bytes() for name in names ],contents)
                image=work/'output/preview.jpg';image.parent.mkdir(parents=True);image.write_bytes(b'preview')
                return [image]
            with patch.object(download_worker,'TelegramCLI',CLI),patch.object(download_worker,'extract_images',side_effect=extract),\
                 patch.object(download_worker,'listing',return_value=('Type = Rar5\nMultivolume = +\nVolumes = 2\n',[])):
                worker.execute()
            self.assertEqual(worker.run['state'],'completed',worker.run['message'])
            self.assertEqual(calls,[307])
            self.assertEqual(extracted,[[names[0]],names])
            self.assertTrue(all(store.history.was_downloaded(i) for i in [101,307]))
            with store.history.connect() as db:
                after=dict(db.execute('SELECT * FROM downloads WHERE id=?',(first['id'],)).fetchone())
            self.assertEqual({key:after[key] for key in metrics},metrics)
            self.assertEqual(list((state/'staging').iterdir()),[])
            for name,content in zip(names,contents):self.assertEqual((base/'Example/2026-09'/name).read_bytes(),content)
            self.assertEqual(len(list((base/'Example/2026-09/release_images').glob('*.jpg'))),1)

    def test_numbered_independent_archives_still_each_extract_their_own_images(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);worker,store,sub,state,base=self.setup_worker(root)
            class CLI:
                def download(self,item,progress,stopped,status=None):
                    source=state/item['filename']
                    with zipfile.ZipFile(source,'w') as archive:archive.writestr('preview.jpg',item['filename'].encode())
                    progress(source.stat().st_size,source.stat().st_size);return source
            worker.telegram=CLI()
            for message in (1,2):
                name=f'Example 2026-09 {message}.rar'
                worker.transfer(sub,{'filename':name,'source_message_id':message,'message_url':sub['topic_url']+'/'+str(message)},'2026-09')
                self.assertTrue(store.history.was_downloaded(message))
            self.assertEqual(len(list((base/'Example/2026-09/release_images').glob('*.jpg'))),2)
            self.assertEqual(worker.numbered_rars,set())

    def test_numbered_archive_requires_confirmed_complete_set_and_is_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);worker,store,sub,state,base=self.setup_worker(root)
            names=['Example 1.rar','Example 2.rar']
            record={'filename':names[0],'sub':sub,'month':'2026-09'}
            self.assertFalse(worker.discover_numbered_rar(record,MissingVolumeError(Path('nested')/names[0],names[1])))
            self.assertTrue(worker.discover_numbered_rar(record,MissingVolumeError(Path('inputs')/names[0],names[1])))
            self.assertEqual(worker.archive_key(names[1],sub,'2026-09')[1],2)
            self.assertEqual(worker.archive_key(names[1],sub,'2026-08')[1],0)
            self.assertEqual(worker.archive_key(names[1],dict(sub,topic_url=sub['topic_url']+'0'),'2026-09')[1],0)
            class CLI:
                def download(self,item,progress,stopped,status=None):
                    source=state/item['filename'];source.write_bytes(b'part');progress(4,4);return source
            worker.telegram=CLI()
            for message,name in enumerate(names,1):
                worker.transfer(sub,{'filename':name,'source_message_id':message,'message_url':sub['topic_url']+'/'+str(message)},'2026-09')
            with patch.object(download_worker,'listing',return_value=('Type = Rar5\nMultivolume = +\nVolumes = 1\n',[])):
                with self.assertRaisesRegex(ExtractionError,'complete archive set'):worker.finish_pending(sub)
            self.assertFalse(any(store.history.was_downloaded(i) for i in (1,2)))
            self.assertEqual(len(list((state/'staging').glob('*/receipt.json'))),2)
            self.assertEqual(list(base.iterdir()),[])

    def test_reuses_previously_completed_part_and_preserves_it_and_its_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);worker,store,sub,state,base=self.setup_worker(root)
            image=root/'preview.jpg';image.write_bytes(os.urandom(6000))
            subprocess.run(['7z','a','-mx0','-v4k',str(root/'Example 2026-09.7z'),str(image)],stdout=subprocess.DEVNULL,check=True)
            first,second=sorted(root.glob('Example 2026-09.7z.*'))
            destination=base/'Example/2026-09'/second.name
            destination.parent.mkdir(parents=True);shutil.copyfile(second,destination)
            old=store.history.register(source_message_id=2,creator='Example',topic_url=sub['topic_url'],filename=second.name,bytes_total=destination.stat().st_size,release_month='2026-09')
            store.history.complete(old['id'],destination=destination,verified_size=destination.stat().st_size,sha256=digest(destination))
            with store.history.connect() as db:before=dict(db.execute('SELECT * FROM downloads WHERE id=?',(old['id'],)).fetchone())
            class UI:
                def download(self,item,progress,stopped,status=None):
                    self.calls=getattr(self,'calls',0)+1
                    source=state/item['filename'];shutil.copyfile(first,source)
                    progress(source.stat().st_size,source.stat().st_size);return source
            worker.telegram=UI()
            worker.transfer(sub,{'filename':first.name,'source_message_id':1,'message_url':sub['topic_url']+'/1'},'2026-09')
            # Same-size corruption is rejected before extraction, and staged new
            # parts remain available for retry after the recorded part is fixed.
            destination.write_bytes(b'x'*second.stat().st_size)
            with self.assertRaisesRegex(ExtractionError,'checksum'):worker.finish_pending(sub)
            self.assertFalse(store.history.was_downloaded(1))
            shutil.copyfile(second,destination)
            worker.finish_pending(sub)
            self.assertEqual(worker.telegram.calls,1)
            self.assertTrue(store.history.was_downloaded(1))
            self.assertEqual(destination.read_bytes(),second.read_bytes())
            self.assertEqual(list((base/'Example/2026-09/release_images').glob('preview__*.jpg'))[0].read_bytes(),image.read_bytes())
            self.assertEqual(list((state/'staging').iterdir()),[])
            with store.history.connect() as db:after=dict(db.execute('SELECT * FROM downloads WHERE id=?',(old['id'],)).fetchone())
            image_fields={'image_count','images_destination','images_manifest','image_warnings','image_status','images_extracted_at'}
            self.assertEqual({k:v for k,v in after.items() if k not in image_fields},{k:v for k,v in before.items() if k not in image_fields})
            self.assertEqual(after['image_status'],'complete');self.assertEqual(after['image_count'],1)

    def test_download_retry_reuses_images_only_after_verified_image_delivery(self):
        for failure in ('image','archive'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as temporary:
                worker,store,sub,state,base=self.setup_worker(Path(temporary));name='Example 2026-09.zip'
                class CLI:
                    calls=0
                    def download(self,item,progress,*args):
                        self.calls+=1;source=state/name
                        with zipfile.ZipFile(source,'w') as z:z.writestr('preview.jpg',b'image')
                        progress(source.stat().st_size,source.stat().st_size);return source
                worker.telegram=CLI();item={'filename':name,'source_message_id':100,'message_url':sub['topic_url']+'/100'}
                move=worker.move_one
                def fail(source,sub,month,filename,subdirectories=()):
                    if bool(subdirectories)==(failure=='image'):raise OSError('test interrupted move')
                    return move(source,sub,month,filename,subdirectories)
                with patch.object(worker,'move_one',side_effect=fail),self.assertRaises(OSError):worker.transfer(sub,item,'2026-09')
                self.assertFalse(store.history.was_downloaded(100))
                with store.history.connect() as db:row=dict(db.execute('SELECT * FROM downloads').fetchone())
                self.assertEqual(row['image_status'],'complete' if failure=='archive' else None)
                with patch.object(download_worker,'extract_images',wraps=extract_images) as extraction:
                    worker.transfer(sub,item,'2026-09')
                self.assertEqual(extraction.call_count,1 if failure=='image' else 0)
                self.assertEqual(worker.telegram.calls,1);self.assertTrue(store.history.was_downloaded(100))

    def test_split_downloads_wait_and_commit_only_after_images_and_all_volumes_arrive(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);worker,store,sub,state,base=self.setup_worker(root)
            image=root/'preview.jpg';image.write_bytes(os.urandom(9000))
            subprocess.run(['7z','a','-mx0','-v4k',str(root/'Example 2026-09.7z'),str(image)],stdout=subprocess.DEVNULL,check=True)
            parts=sorted(root.glob('Example 2026-09.7z.*'),reverse=True)
            class UI:
                def download(self,item,progress,stopped,status=None):
                    source=state/item['filename'];shutil.copyfile(root/item['filename'],source)
                    progress(source.stat().st_size,source.stat().st_size);return source
            worker.telegram=UI()
            for message,part in enumerate(parts,1):
                worker.transfer(sub,{'filename':part.name,'source_message_id':message,'message_url':sub['topic_url']+'/'+str(message)},'2026-09')
                self.assertFalse(store.history.was_downloaded(message))
                self.assertEqual(list(base.iterdir()),[])
            # A failed history transaction leaves ALL volumes available to retry,
            # even when delivery of the images and archives already succeeded.
            with patch.object(worker.history,'complete_release',side_effect=OSError('test commit failure')):
                with self.assertRaises(OSError):worker.finish_pending(sub)
            for message in range(1,len(parts)+1):self.assertFalse(store.history.was_downloaded(message))
            self.assertEqual(len(list((state/'staging').glob('*/receipt.json'))),len(parts))
            worker.finish_pending(sub)
            self.assertTrue(all(store.history.was_downloaded(i) for i in range(1,len(parts)+1)))
            self.assertEqual(list((state/'staging').iterdir()),[])
            delivered=list((base/'Example/2026-09/release_images').glob('preview__*.jpg'))
            self.assertEqual(len(delivered),1);self.assertEqual(delivered[0].read_bytes(),image.read_bytes())
            for part in parts:self.assertEqual((base/'Example/2026-09'/part.name).read_bytes(),part.read_bytes())

    def test_renamed_images_are_delivered_flat_without_warnings_and_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);worker,store,sub,state,base=self.setup_worker(root)
            fixture=state/'fixture.zip'
            with zipfile.ZipFile(fixture,'w') as archive:
                archive.writestr(r'renders\preview:front.jpg',b'original image bytes')
                archive.writestr(r'models\unselected.stl',b'model bytes')
            original=fixture.read_bytes()
            class CLI:
                def download(self,item,progress,stopped,status=None):
                    source=state/item['filename'];source.write_bytes(original);progress(len(original),len(original));return source
            worker.telegram=CLI();item={'filename':'Example 2026-09.zip','source_message_id':1,
                'message_url':sub['topic_url']+'/1','bytes_total':len(original)}
            worker.transfer(sub,item,'2026-09')
            folder=base/'Example/2026-09';images=list((folder/'release_images').iterdir())
            self.assertEqual(len(images),1);self.assertTrue(images[0].is_file());self.assertNotIn(':',images[0].name)
            self.assertEqual(images[0].read_bytes(),b'original image bytes');self.assertEqual((folder/item['filename']).read_bytes(),original)
            with store.history.connect() as db:row=dict(db.execute('SELECT * FROM downloads WHERE source_message_id=1').fetchone())
            self.assertEqual(row['state'],'downloaded');self.assertEqual(row['image_status'],'complete')
            self.assertEqual(json.loads(row['image_warnings']),[]);self.assertEqual(worker.run['warnings'],[])
            with patch.object(download_worker,'extract_images',side_effect=AssertionError('Already complete')):
                worker.transfer(sub,item,'2026-09')

    def test_failed_image_delivery_keeps_archive_and_does_not_mark_downloaded(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);worker,store,sub,state,base=self.setup_worker(root)
            class UI:
                def download(self,item,progress,stopped,status=None):
                    source=state/item['filename']
                    with zipfile.ZipFile(source,'w') as archive:archive.writestr('preview.jpg',b'image')
                    progress(source.stat().st_size,source.stat().st_size);return source
            worker.telegram=UI()
            with patch.object(worker,'move_one',side_effect=OSError('test NAS failure')):
                with self.assertRaises(OSError):worker.transfer(sub,{'filename':'Example 2026-09.zip','source_message_id':1,'message_url':sub['topic_url']+'/1'},'2026-09')
            self.assertFalse(store.history.was_downloaded(1))
            self.assertEqual(len(list((state/'staging').glob('*/receipt.json'))),1)
            self.assertEqual(list(base.iterdir()),[])

    def test_reuses_organizer_imported_part_without_inventing_a_historical_checksum(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);worker,store,sub,state,base=self.setup_worker(root)
            image=root/'preview.jpg';image.write_bytes(os.urandom(6000))
            subprocess.run(['7z','a','-mx0','-v4k',str(root/'Example 2026-09.7z'),str(image)],stdout=subprocess.DEVNULL,check=True)
            first,second=sorted(root.glob('Example 2026-09.7z.*'))
            month=base/'Example/2026-09';month.mkdir(parents=True);shutil.copyfile(second,month/second.name)
            store.history.import_organized({'id':'import','base':str(base),'creator_folder':'Example','creator':'Example','topic_url':sub['topic_url']},
                [{'source_message_ids':[2],'source':second.name,'destination':'2026-09/'+second.name,'filename':second.name,'size':second.stat().st_size,'month':'2026-09'}],None,None)
            class UI:
                def download(self,item,progress,stopped,status=None):
                    source=state/item['filename'];shutil.copyfile(first,source);progress(source.stat().st_size,source.stat().st_size);return source
            worker.telegram=UI()
            worker.transfer(sub,{'filename':first.name,'source_message_id':1,'message_url':sub['topic_url']+'/1'},'2026-09')
            worker.finish_pending(sub)
            self.assertTrue(store.history.was_downloaded(1));self.assertTrue(store.history.was_downloaded(2))
            with store.history.connect() as db:self.assertIsNone(db.execute('SELECT sha256 FROM downloads WHERE source_message_id=2').fetchone()[0])
            self.assertEqual(list((month/'release_images').glob('*.jpg'))[0].read_bytes(),image.read_bytes())


if __name__=='__main__':unittest.main()
