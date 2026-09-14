import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from folder_organizer import Organizer, inventory, match_files
from organizer_apply import ApplyWorker, ready_groups, start_application
from organizer_versions import choices, upgrade
from subscription_store import SubscriptionStore
from test_support import configure_source

TOPIC='https://t.me/c/123456789/200'


class ArchiveVersionTests(unittest.TestCase):
    def store(self,root):
        store=SubscriptionStore(configure_source(root));base=root/'nas';base.mkdir();folder=base/'Example';folder.mkdir()
        store.atomic_write(store.config_path,{'revision':0,'download_directory':str(base)})
        store.atomic_write(root/'data/creators.json',{'creators':[{'name':'Example','topic_url':TOPIC,'within_approved_group':True}]})
        return store,folder

    def attachments(self,files,start):
        return [{'filename':p.name,'bytes_total':p.stat().st_size,'source_message_id':start+i,
            'message_url':TOPIC+'/'+str(start+i),'topic_url':TOPIC,'dc_id':1,'document_id':1000+start+i}
            for i,p in enumerate(files)]

    def preview(self,store,folder,items):
        files=inventory(folder);match_files(files,items,set())
        plan={'id':'preview','state':'completed','backend':'cli','base':str(folder.parent),'creator':'Example',
            'creator_folder':'Example','topic_url':TOPIC,'files':files,'attachments':items,'warnings':[]}
        store.atomic_write(Organizer(store).path,plan);return plan

    def start(self,store,resume=None):
        with patch('organizer_apply.Popen'):
            result=start_application(store,{'job_id':resume} if resume else {'plan_id':'preview','extract_images':True},resume=bool(resume))
        return result['application']['id']

    def job(self,store):return json.loads((store.root/'data/organizer-apply.json').read_text())

    def split(self,root,folder,label,size):
        inputs=root/label;inputs.mkdir();(inputs/'preview.jpg').write_bytes(label.encode()*100)
        (inputs/'model.stl').write_bytes(os.urandom(size))
        target=folder/'Example 2026-01.7z'
        subprocess.run(['7z','a','-mx0','-v1k',str(target),'preview.jpg','model.stl'],cwd=inputs,stdout=subprocess.DEVNULL,check=True)
        return sorted(folder.glob('Example 2026-01.7z.*'))

    def fixture(self,root):
        store,folder=self.store(root);month=folder/'2026-01';month.mkdir()
        old=self.split(root,month,'old',1800);new=self.split(root,folder,'new',4600)
        originals={p.name:p.read_bytes() for p in old}
        plan=self.preview(store,folder,self.attachments(old,100)+self.attachments(new,200))
        return store,folder,old,new,originals,plan

    def test_larger_complete_local_set_replaces_old_without_mixing_or_wrong_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            store,folder,old,new,originals,plan=self.fixture(Path(temporary))
            groups=choices(plan);group=next(iter(groups.values()))
            self.assertFalse(group['version']['download'])
            self.assertEqual([r['source'] for r in group['records']],[p.name for p in new])
            self.assertTrue(all(len(r['source_message_ids'])==1 and r['source_message_ids'][0]>=200 for r in group['records']))
            preferred={p.name:p.read_bytes() for p in new}
            job_id=self.start(store);ApplyWorker(store,job_id).execute();job=self.job(store)
            self.assertEqual(job['releases_review'],0,job['message'])
            self.assertEqual(job['files_done'],len(new))
            for name,data in preferred.items():self.assertEqual((folder/'2026-01'/name).read_bytes(),data)
            backups=list((folder/'2026-01/.previous_versions').rglob('*.0*'))
            self.assertEqual({p.name:p.read_bytes() for p in backups},originals)
            self.assertEqual({p.read_bytes() for p in (folder/'2026-01/release_images').iterdir()},{b'new'*100})
            for item in plan['attachments']:
                self.assertEqual(store.history.was_downloaded(item['source_message_id']),item['source_message_id']>=200)

    def test_retry_after_publish_keeps_backups_and_reuses_extracted_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            store,folder,old,new,originals,plan=self.fixture(Path(temporary));job_id=self.start(store)
            with patch.object(store.history,'import_organized',side_effect=OSError('interrupted history commit')):
                ApplyWorker(store,job_id).execute()
            self.assertEqual(self.job(store)['releases_review'],1)
            self.start(store,resume=job_id)
            with patch('organizer_apply.extract_images',side_effect=AssertionError('must reuse images')):
                ApplyWorker(store,job_id).execute()
            job=self.job(store);self.assertEqual(job['releases_review'],0,job['message'])
            self.assertEqual(job['files_done'],len(new));self.assertEqual(job['moves_done'],len(new))
            self.assertEqual({p.name:p.read_bytes() for p in (folder/'2026-01/.previous_versions').rglob('*.0*')},originals)

    def test_partial_local_versions_are_never_joined(self):
        with tempfile.TemporaryDirectory() as temporary:
            store,folder,old,new,originals,plan=self.fixture(Path(temporary))
            for p in new[:len(old)]:p.unlink()
            plan=self.preview(store,folder,plan['attachments']);group=next(iter(choices(plan).values()))
            self.assertTrue(group['version']['download']);self.assertEqual(len(group['records']),len(new))
            self.assertTrue(all(r['version_download'] for r in group['records']))

    def test_preference_uses_total_size_then_latest_complete_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            store,folder=self.store(Path(temporary));local=folder/'EXAMPLE 2026-01.zip';local.write_bytes(b'local')
            items=[]
            for name,size,mid in [('EXAMPLE 2026-01.zip',5,100),('Example 2026-01.zip',20,200),('example 2026-01.ZIP',20,300),('EXample 2026-01.zip',10,400)]:
                items.append({'filename':name,'bytes_total':size,'source_message_id':mid,'message_url':TOPIC+'/'+str(mid),'topic_url':TOPIC})
            plan=self.preview(store,folder,items);group=next(iter(choices(plan).values()))
            self.assertEqual(group['version']['id'],'300');self.assertEqual(group['version']['bytes_total'],20)

    def test_old_stopped_job_upgrades_only_failed_groups_and_keeps_transfer_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            store,folder,old,new,originals,plan=self.fixture(Path(temporary))
            version=next(iter(choices(plan).values()));record=version['records'][0]
            completed={'key':'2025-12/old.zip','state':'completed','records':[{'filename':'old.zip','month':'2025-12','size':1,'action':'move','moved':True,'recorded':True}]}
            old_group={'key':version['key'],'state':'needs_review','records':[r for r in plan['files'] if r['action'] in ('keep','move')],
                'repairs':[{'item':version['version']['items'][0],'download':{'path':'/saved/receipt','size':record['size'],'sha256':'proof'}}]}
            job={'groups':[completed,old_group]};before=copy.deepcopy(completed)
            self.assertTrue(upgrade(job,plan));self.assertEqual(job['groups'][0],before)
            self.assertEqual(job['groups'][1]['version_downloads'][str(record['source_message_id'])]['download']['path'],'/saved/receipt')
            self.assertFalse(upgrade(job,plan));self.assertEqual(job['files_done'],1)

    def test_invalid_larger_archive_does_not_move_any_previous_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            store,folder,old,new,originals,plan=self.fixture(Path(temporary))
            first=new[0];data=first.read_bytes();first.write_bytes(b'broken!'+data[7:])
            plan=self.preview(store,folder,plan['attachments']);job_id=self.start(store)
            ApplyWorker(store,job_id).execute();job=self.job(store)
            self.assertEqual(job['releases_review'],1)
            self.assertFalse((folder/'2026-01/.previous_versions').exists())
            self.assertEqual({p.name:p.read_bytes() for p in old},originals)

    def test_image_receipts_distinguish_versions_with_identical_filenames_and_sizes(self):
        with tempfile.TemporaryDirectory() as temporary:
            store,folder=self.store(Path(temporary));destination=folder/'2026-01/release_images'
            first={'filename':'Example 2026-01.zip','size':10,'archive_version':'100','source_message_ids':[100]}
            second=dict(first,archive_version='200',source_message_ids=[200])
            for mid in (100,200):store.history.register(source_message_id=mid,creator='Example',topic_url=TOPIC,filename=first['filename'],bytes_total=10)
            store.history.record_image_extraction(TOPIC,[first],[{'path':'old.jpg'}],destination)
            self.assertIsNone(store.history.image_extraction(TOPIC,[second],destination))
            store.history.record_image_extraction(TOPIC,[second],[{'path':'new.jpg'}],destination)
            with store.history.connect() as db:
                rows={r['source_message_id']:json.loads(r['images_manifest']) for r in db.execute('SELECT * FROM downloads')}
            self.assertEqual(rows,{100:[{'path':'old.jpg'}],200:[{'path':'new.jpg'}]})

    def test_downloads_preferred_set_reuses_receipt_and_resumes_after_publish_failure(self):
        from file_delivery import deliver,digest
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store,folder=self.store(root);month=folder/'2026-01';month.mkdir()
            old=self.split(root,month,'old',800);server=root/'server';server.mkdir()
            preferred=self.split(root,server,'new',1700)
            plan=self.preview(store,folder,self.attachments(old,100)+self.attachments(preferred,200))
            originals={p.name:p.read_bytes() for p in old};target_bytes={p.name:p.read_bytes() for p in preferred}
            job_id=self.start(store);job=self.job(store);group=job['groups'][0]
            staging=root/'downloads';staging.mkdir();first=staging/'first.part';first.write_bytes(preferred[0].read_bytes())
            group['version_downloads']={'200':{'item':group['version']['items'][0],
                'download':{'path':str(first),'size':first.stat().st_size,'sha256':digest(first)}}}
            store.atomic_write(root/'data/organizer-apply.json',job)
            class Client:
                calls=[]
                def download(self,item,progress,stopped,status):
                    self.calls.append(item['source_message_id']);source=staging/(str(item['source_message_id'])+'.part')
                    source.write_bytes((server/item['filename']).read_bytes());progress(0,item['bytes_total']);progress(item['bytes_total'],item['bytes_total']);return source
                def cleanup_transfer(self,item):pass
                def close(self):pass
            client=Client();published=False
            def interrupted(*args,**kwargs):
                nonlocal published
                result=deliver(*args,**kwargs)
                if not published:published=True;raise OSError('interrupted after publish')
                return result
            with patch('organizer_versions.TelegramCLI',return_value=client):
                with patch('organizer_versions.deliver',side_effect=interrupted):ApplyWorker(store,job_id).execute()
                self.assertEqual(self.job(store)['releases_review'],1);self.assertTrue(first.exists())
                with store.history.connect() as db:self.assertTrue(all(r['state']=='paused' for r in db.execute('SELECT * FROM downloads')))
                self.start(store,resume=job_id);ApplyWorker(store,job_id).execute()
            job=self.job(store);self.assertEqual(job['releases_review'],0,job['message'])
            self.assertEqual(client.calls,list(range(201,200+len(preferred))))
            self.assertEqual(job['files_done'],len(preferred));self.assertFalse(first.exists())
            self.assertEqual({p.name:p.read_bytes() for p in (month/'.previous_versions').rglob('*.0*')},originals)
            for name,data in target_bytes.items():self.assertEqual((month/name).read_bytes(),data)
            for mid in range(200,200+len(preferred)):self.assertTrue(store.history.was_downloaded(mid))


if __name__=='__main__':unittest.main()
