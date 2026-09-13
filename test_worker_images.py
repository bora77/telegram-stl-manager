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

import download_worker
from file_delivery import digest
from release_images import ExtractionError
from subscription_store import SubscriptionStore


class WorkerImageTests(unittest.TestCase):
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
            with store.history.connect() as db:self.assertEqual(dict(db.execute('SELECT * FROM downloads WHERE id=?',(old['id'],)).fetchone()),before)

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
