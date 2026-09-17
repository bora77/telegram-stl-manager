import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from mmf_client import MMFClient, MMFError, LoginRequired, LoginForm
from mmf_manager import MMFManager, month_for, version_key
from subscription_store import SubscriptionStore

class Response(io.BytesIO):
    def __init__(self,body,status=200,headers=None,url='https://dl4.myminifactory.com/file.zip'):
        super().__init__(body);self.status=status;self.headers=headers or {};self.url=url

class MMFTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.store=SubscriptionStore(self.root);self.manager=MMFManager(self.store)
        self.manager.put('creators',[{'id':1,'name':'Example Creator'}])
        self.item={'object_id':22,'archive_id':33,'size':3,'updated_at':'v1','filename':'January2025_Model.zip','creator':'Example Creator','creator_id':1,'folder':'Example Creator','month':'2025-01','release':'January 2025','object_name':'Model','start_month':''}
        self.item['key']=version_key(self.item)
    def test_months_and_loyalty(self):
        self.assertEqual(month_for('August2025Reward.zip','1 Year Rewards'),'2025-08')
        self.assertEqual(month_for('model.zip','September 2026'),'2026-09')
        self.assertIsNone(month_for('model.zip','1 Year Rewards'))
        self.assertIsNone(month_for('model.zip','January February 2025'))
    def test_version_changes(self):
        self.assertNotEqual(version_key(self.item),version_key({**self.item,'updated_at':'v2'}))
        self.assertNotEqual(version_key(self.item),version_key({**self.item,'size':4}))
    def test_unsafe_folders_rejected(self):
        for folder in ('../bad','a/b','a\\b','..',' x'):
            with self.assertRaises(MMFError):self.manager.save({'revision':0,'subscriptions':[{'id':1,'folder':folder,'start_month':''}]})
    def test_durable_completion(self):
        with self.manager.db() as db:db.execute('INSERT INTO completed VALUES (?,?)',(self.item['key'],'{}'))
        self.assertIn(self.item['key'],MMFManager(self.store).completed())
    def test_login_page_never_metadata(self):
        c=MMFClient(self.root/'cookies')
        with patch.object(c,'open',return_value=Response(b'<html>login</html>',headers={'Content-Type':'text/html'})):
            with self.assertRaises(LoginRequired):c.metadata('/api/library')
    def test_resume_range_and_ignored_range(self):
        for status,headers in [(206,{'Content-Range':'bytes 1-2/3','Content-Length':'2'}),(200,{'Content-Length':'3'})]:
            c=MMFClient(self.root/'cookies');target=self.root/'part';target.write_bytes(b'a')
            with patch.object(c,'open',return_value=Response(b'bc' if status==206 else b'abc',status,headers)):
                c.download(self.item,target,lambda *x:None,lambda:False)
            self.assertEqual(target.read_bytes(),b'abc')
    def test_bad_resume_and_html_keep_partial(self):
        for headers in ({'Content-Range':'bytes 0-2/3'},{'Content-Type':'text/html'}):
            c=MMFClient(self.root/'cookies');target=self.root/'part';target.write_bytes(b'a')
            with patch.object(c,'open',return_value=Response(b'bad',206,headers)):
                with self.assertRaises(MMFError):c.download(self.item,target,lambda *x:None,lambda:False)
            self.assertEqual(target.read_bytes(),b'a')
    def test_short_transfer_retained(self):
        c=MMFClient(self.root/'cookies');target=self.root/'part'
        with patch.object(c,'open',return_value=Response(b'ab')):
            with self.assertRaises(MMFError):c.download(self.item,target,lambda *x:None,lambda:False)
        self.assertEqual(target.read_bytes(),b'ab')
    def test_check_old_collection_new_file_and_no_download(self):
        self.manager.put('settings',{'revision':0,'subscriptions':[{'id':1,'name':'Example Creator','folder':'Example Creator','start_month':'2025-01'}]})
        class Client:
            def groups(self):return [{'id':1,'name':'Example Creator'}]
            def metadata(self,path):
                return [{'originalId':22,'creatorId':1,'source':'USER_GROUP','type':'object','release':44,'name':'Old loyalty collection'}] if 'objectPreviews' in path else [{'id':44,'label':'1 Year Rewards'}]
            def downloadables(self,oid):return {'archives':[{'id':33,'name':'January2025Reward.zip','size':'3','updatedAt':'v1'}]}
            def save(self):pass
        with patch.object(self.manager,'client',return_value=Client()):self.manager.check()
        self.assertEqual(len(self.manager.get('items')),1);self.assertEqual(self.manager.get('items')[0]['month'],'2025-01')
        self.assertFalse(self.manager.completed());self.assertFalse((self.manager.directory/'staging').exists())
    def test_successful_delivery_then_failure_continues(self):
        import zipfile
        source=self.root/'source.zip'
        with zipfile.ZipFile(source,'w') as z:z.writestr('model.stl','solid')
        first={**self.item,'size':source.stat().st_size};first['key']=version_key(first)
        bad={**first,'object_id':23,'archive_id':34,'release':'Another release'};bad['key']=version_key(bad)
        self.manager.put('queue',[bad,first]);self.manager.put('run_base',str(self.root))
        class Client:
            def downloadables(self,oid):
                if oid==23:raise MMFError('Bad release')
                return {'archives':[{'id':33,'size':first['size'],'updatedAt':'v1'}]}
            def download(self,item,path,*args):path.write_bytes(source.read_bytes())
            def save(self):pass
        def delivery(path,*args,**kwargs):return self.root/'delivered.zip',path.stat().st_size,'verified-checksum'
        with patch.object(self.manager,'client',return_value=Client()),patch('mmf_manager.extract_images',return_value=[]),patch('mmf_manager.deliver',side_effect=delivery):self.manager.download()
        self.assertIn(first['key'],self.manager.completed());self.assertNotIn(bad['key'],self.manager.completed())
        self.assertEqual(len(self.manager.get('job')['errors']),1)
        self.assertFalse(list((self.manager.directory/'staging').glob('**/*.zip')))

    def test_real_extract_deliver_and_resume_receipt(self):
        import zipfile
        from PIL import Image
        image=self.root/'preview.png';Image.new('RGB',(12,16),'blue').save(image)
        archive=self.root/'fixture.zip'
        with zipfile.ZipFile(archive,'w') as z:z.write(image,'nested/preview.png');z.writestr('model.stl','solid model')
        item={**self.item,'size':archive.stat().st_size};item['key']=version_key(item)
        self.manager.put('queue',[item]);self.manager.put('run_base',str(self.root))
        class Client:
            calls=0
            def downloadables(self,oid):return {'archives':[{'id':33,'size':item['size'],'updatedAt':'v1'}]}
            def download(self,item,path,*args):self.calls+=1;path.write_bytes(archive.read_bytes())
            def save(self):pass
        client=Client()
        with patch.object(self.manager,'client',return_value=client):
            self.manager.download();self.manager.download()
        self.assertEqual(client.calls,1)
        self.assertIn(item['key'],self.manager.completed())
        images=list((self.root/'Example Creator'/'January 2025'/'release_images').iterdir())
        self.assertEqual(len(images),1);self.assertTrue(images[0].is_file())
        self.assertEqual(len(list((self.root/'Example Creator'/'January 2025').glob('*.7z'))),1)
        self.assertFalse(list((self.manager.directory/'staging').glob('**/*.zip')))
        with self.manager.db() as db:record=json.loads(db.execute('SELECT data FROM completed').fetchone()[0])
        self.assertEqual(record['image_count'],1);self.assertTrue(record['images_extracted']);self.assertEqual(len(record['sha256']),64)


class ReleasePackagingTests(MMFTests):
    def test_welcome_pack_combines_models_and_resumes(self):
        import zipfile
        from release_images import listing, command
        items=[];sources={}
        for oid,body in [(101,'first model'),(102,'second model')]:
            source=self.root/f'{oid}.zip'
            with zipfile.ZipFile(source,'w') as z:z.writestr('model.stl',body)
            item={**self.item,'object_id':oid,'object_name':f'Model {oid}','archive_id':oid,'filename':'original.zip','release_id':'welcome','release':'Welcome Pack','month':None,'size':source.stat().st_size}
            item['key']=version_key(item);items.append(item);sources[oid]=source
        self.manager.put('items',items);self.assertEqual(self.manager.state()['counts']['available'],2)
        self.manager.put('queue',items);self.manager.put('run_base',str(self.root))
        class Client:
            calls=0
            def downloadables(self,oid):return {'archives':[{'id':oid,'size':sources[oid].stat().st_size,'updatedAt':'v1'}]}
            def download(self,item,path,*args):self.calls+=1;path.write_bytes(sources[item['object_id']].read_bytes())
            def save(self):pass
        client=Client()
        with patch.object(self.manager,'client',return_value=client):self.manager.download();self.manager.download()
        self.assertEqual(self.manager.get('job')['errors'],[])
        self.assertEqual(client.calls,2);self.assertEqual(self.manager.completed(),{i['key'] for i in items})
        destination=self.root/'Example Creator'/'Welcome Pack'
        self.assertEqual([p.name for p in destination.glob('*.7z')],['Welcome Pack.7z'])
        _,entries=listing(destination/'Welcome Pack.7z',self.root,lambda:False)
        self.assertEqual({e['name'] for e in entries},{'Model 101/original/model.stl','Model 102/original/model.stl'})
        output=self.root/'verify'
        command(['x','-y','-o'+str(output),'--',str(destination/'Welcome Pack.7z')],self.root,lambda:False)
        self.assertEqual((output/'Model 101/original/model.stl').read_text(),'first model')
        self.assertEqual((output/'Model 102/original/model.stl').read_text(),'second model')

    def test_named_delivery_rejects_traversal_and_keeps_month_validation(self):
        from file_delivery import deliver,DeliveryError
        source=self.root/'source';source.write_text('test')
        for name in ('../outside','a/b','a\\b','..',' x'):
            with self.assertRaises(DeliveryError):deliver(source,self.root,'Creator',None,'file',release_directory=name)
        with self.assertRaises(DeliveryError):deliver(source,self.root,'Creator','Welcome Pack','file')

    def test_updated_release_queue_includes_unchanged_sources(self):
        first={**self.item,'release':'Welcome Pack','release_id':'welcome','month':None}
        added={**first,'archive_id':34,'filename':'new.zip'};added['key']=version_key(added)
        self.manager.put('items',[first,added]);self.manager.put('settings',{'subscriptions':[{'id':1}]});self.manager.put('checked_at',1)
        self.manager.session.touch()
        with self.manager.db() as db:db.execute('INSERT INTO completed VALUES (?,?)',(first['key'],'{}'))
        with patch.object(self.store,'config',return_value={'download_directory':str(self.root)}),patch('mmf_manager.subprocess.Popen') as spawn:
            self.manager.start('download')
            self.assertTrue(spawn.called)
        self.assertEqual({i['key'] for i in self.manager.get('queue')},{first['key'],added['key']})

    def test_release_identity_does_not_follow_member_dates(self):
        from mmf_manager import releases
        first={**self.item,'release_id':'welcome','release':'Welcome Pack','month':'2023-01'}
        second={**first,'archive_id':34,'month':'2026-09'}
        monthly={**first,'release_id':'monthly','release':'September 2026'}
        groups=releases([first,second,monthly])
        self.assertEqual(sorted(map(len,groups.values())),[1,2])
        self.assertEqual(first['release_folder'],'Welcome Pack')
        self.assertEqual(monthly['release_folder'],'September 2026')

class ReleaseDateTests(unittest.TestCase):
    def test_creation_date_and_special_names(self):
        from mmf_manager import release_date_month
        self.assertEqual(release_date_month('Balius Battle Strider','2023-09-15T16:52:43+00:00'),('2023-09','created_at'))
        self.assertEqual(release_date_month('January 2025','2026-09-01T00:00:00Z'),('2025-01','name'))
        for name in ('Welcome Pack','Loyalty Rewards (Patreon)','5 months - Gnom','1 Year Rewards'):
            self.assertEqual(release_date_month(name,'2023-10-01T00:00:00Z'),(None,None))
        for date in (None,'bad','2023-02-30'):
            self.assertEqual(release_date_month('Named release',date),(None,None))

    def test_same_month_keeps_separate_release_sets(self):
        from mmf_manager import releases
        items=[{'creator_id':1,'release_id':str(n),'release':name,'release_created_at':'2023-09-15T00:00:00Z','updated_at':'2026-09-10'} for n,name in enumerate(('First model','Second model'))]
        groups=releases(items)
        self.assertEqual(len(groups),2)
        self.assertEqual([i['release_folder'] for i in items],['2023-09','2023-09'])
        self.assertTrue(all(i['release_month']=='2023-09' for i in items))

if __name__=='__main__':unittest.main()
