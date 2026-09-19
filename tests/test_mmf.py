import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.mmf_client import MMFClient, MMFError, LoginRequired, LoginForm
from app.mmf_manager import MMFManager, month_for, version_key
from app.subscription_store import SubscriptionStore

class Response(io.BytesIO):
    def __init__(self,body,status=200,headers=None,url='https://dl4.myminifactory.com/file.zip'):
        super().__init__(body);self.status=status;self.headers=headers or {};self.url=url

class MMFTests(unittest.TestCase):
    def test_tribe_campaign_alias_uses_delivery_month_not_model_creation(self):
        self.manager.put('settings',{'subscriptions':[{'id':1,'name':'Example Creator','folder':'Example Creator','start_month':'2026-08'}]})
        base={'originalId':22,'creatorId':1,'type':'object','name':'Bell Model','createdAt':'2026-06-29T11:45:22+00:00','publishedAt':None,'libraryAddedAt':'2026-09-11T14:42:22+00:00'}
        objects=[{**base,'source':'TRIBE','release':'type:campaign-tier;orderId:42;tierId:9'},
                 {**base,'source':'FRONTIER','release':'9','campaignId':50}]
        responses={'objectPreviews':objects,'tribe_releases_metadata/1':[],
                   'frontier_releases_metadata/50':{'pledges':[{'id':9,'name':'Monthly Tier','createdAt':'2026-06-01'}]}}
        calls=[]
        class Client:
            def groups(self):return [{'id':1,'name':'Example Creator','sources':['USER_GROUP','TRIBE','FRONTIER']}]
            def metadata(self,path):return responses[path.removeprefix('/api/data-library/')]
            def downloadables(self,oid):calls.append(oid);return {'archives':[{'id':33,'size':3,'name':'model.zip','updatedAt':'v1'}]}
            def save(self):pass
        with patch.object(self.manager,'client',return_value=Client()):self.manager.check()
        result=self.manager.get('items');self.assertEqual(len(result),1)
        self.assertEqual(result[0]['library_source'],'TRIBE');self.assertEqual(result[0]['release_month'],'2026-09')
        self.assertEqual(result[0]['release_month_basis'],'delivery_at');self.assertEqual(calls,[22])
        # Explicit release months remain authoritative over delivery dates.
        responses['tribe_releases_metadata/1']=[{'id':objects[0]['release'],'label':'August 2026'}]
        with patch.object(self.manager,'client',return_value=Client()):self.manager.check()
        self.assertEqual(self.manager.get('items')[0]['release_month'],'2026-08')

    def test_old_shared_entitlement_cannot_hide_current_tribe_release(self):
        self.manager.put('settings',{'subscriptions':[{'id':1,'name':'Example Creator','folder':'Example Creator','start_month':'2026-08'}]})
        base={'originalId':22,'creatorId':1,'type':'object','name':'Model'}
        objects=[{**base,'source':'USER_GROUP','release':'old'},{**base,'source':'TRIBE','release':'new'}]
        responses={'objectPreviews':objects,'userGroup_releases_metadata/1':[{'id':'old','label':'January 2025'}],
            'tribe_releases_metadata/1':[{'id':'new','label':'September 2026'}]}
        calls=[]
        class Client:
            def groups(self):return [{'id':1,'name':'Example Creator'}]
            def metadata(self,path):return responses[path.removeprefix('/api/data-library/')]
            def downloadables(self,oid):calls.append(oid);return {'archives':[{'id':33,'size':3,'name':'model.zip','updatedAt':'v1'}]}
            def save(self):pass
        with patch.object(self.manager,'client',return_value=Client()):self.manager.check()
        items=self.manager.get('items');self.assertEqual(len(items),1)
        self.assertEqual(items[0]['library_source'],'TRIBE');self.assertEqual(items[0]['release_month'],'2026-09')
        self.assertEqual(calls,[22])
        with self.manager.db() as db:db.execute('INSERT INTO completed VALUES (?,?)',(items[0]['key'],json.dumps(items[0])))
        self.manager.put('settings',{'subscriptions':[{'id':1,'name':'Example Creator','folder':'Example Creator','start_month':''}]})
        calls.clear()
        with patch.object(self.manager,'client',return_value=Client()):self.manager.check()
        result=self.manager.get('items');self.assertEqual(len(result),1)
        self.assertEqual(result[0]['release_key'],items[0]['release_key']);self.assertEqual(calls,[22])

    def test_creator_refresh_does_not_change_jobs_or_selections(self):
        self.manager.session.write_text('# Netscape HTTP Cookie File\n')
        preserved={'settings':{'subscriptions':[{'id':1}]},'items':[self.item],'queue':[self.item],'job':{'phase':'downloading'},'checked_at':123}
        for key,value in preserved.items():self.manager.put(key,value)
        with patch.object(MMFClient,'groups',return_value=[{'id':2,'name':'New Creator'}]):
            self.assertEqual(self.manager.refresh_creators()['creators'],[{'id':2,'name':'New Creator'}])
        for key,value in preserved.items():self.assertEqual(self.manager.get(key),value)
        with patch.object(MMFClient,'groups',side_effect=MMFError('Offline')):
            with self.assertRaises(MMFError):self.manager.refresh_creators()
        self.assertEqual(self.manager.get('creators'),[{'id':2,'name':'New Creator'}])

    def test_all_library_sources_and_duplicate_entitlements(self):
        self.manager.put('settings',{'subscriptions':[{'id':1,'name':'Example Creator','folder':'Example Creator','start_month':'2026-08'}]})
        def obj(oid,source,rid,**extra):
            return {'originalId':oid,'creatorId':1,'creatorName':'Example Creator','source':source,'type':'object','release':rid,'name':'Model',**extra}
        objects=[obj(1,'USER_GROUP','7'),obj(2,'TRIBE','7'),obj(3,'TRIBE','welcome'),obj(4,'FRONTIER','7',campaignId=50),obj(4,'TRIBE','campaign-alias'),obj(5,'FRONTIER','8',campaignId=50),obj(6,'FRONTIER','9',campaignId=50),obj(7,'PURCHASE','7')]
        responses={'objectPreviews':objects,'userGroup_releases_metadata/1':[{'id':7,'label':'January 2025'}],
            'tribe_releases_metadata/1':[{'id':'7','label':'August 2026'},{'id':'welcome','label':'Welcome Pack'}],
            'frontier_releases_metadata/50':{'pledges':[{'id':7,'name':'Campaign Pledge','createdAt':'2020-01-01'}],'addons':[{'id':8,'name':'Campaign Extra'}],'signup':{'id':9,'name':'Sign-Up Bonus'}}}
        calls=[]
        class Client:
            def groups(self):return [{'id':1,'name':'Example Creator'}]
            def metadata(self,path):return responses[path.removeprefix('/api/data-library/')]
            def downloadables(self,oid):calls.append(oid);return {'archives':[{'id':oid,'size':10,'name':'model.zip','updatedAt':'v1'}]}
            def save(self):pass
        with patch.object(self.manager,'client',return_value=Client()):self.manager.check()
        items=self.manager.get('items');byid={i['object_id']:i for i in items}
        self.assertEqual(set(byid),{2,3,4,5,6});self.assertEqual(calls.count(4),1);self.assertNotIn(7,calls)
        self.assertEqual(byid[2]['release_month'],'2026-08')
        self.assertEqual(byid[3]['release_folder'],'Example Creator Welcome Pack')
        self.assertEqual(byid[4]['release_folder'],'Example Creator Campaign Pledge')
        self.assertIsNone(byid[4]['release_month'])
        self.assertIn('1 older releases excluded',self.manager.get('job')['message'])
        original=byid[2]
        with self.manager.db() as db:db.execute('INSERT INTO completed VALUES (?,?)',(original['key'],json.dumps(original)))
        responses['tribe_releases_metadata/1'][0]['label']='January 2025'
        # A completed release retains its identity and its later additions even
        # when the starting month moves forward.
        responses['tribe_releases_metadata/1'][0]['label']='August 2026'
        self.manager.put('settings',{'subscriptions':[{'id':1,'name':'Example Creator','folder':'Example Creator','start_month':'2026-09'}]})
        with patch.object(self.manager,'client',return_value=Client()):self.manager.check()
        self.assertIn(original['key'],[i['key'] for i in self.manager.get('items')])

    def test_creator_discovery_merges_sources_and_retained_tribe_objects(self):
        client=MMFClient(self.root/'cookies')
        values={'userGroups_metadata':[{'id':1,'name':'Shared'}],
            'tribes_metadata':[{'id':1,'name':'Shared','source':'TRIBE'},{'id':2,'name':'Tribe','source':'TRIBE'},{'id':99,'name':'Other','source':'THE_ADVENTURE'}],
            'frontiers_metadata':[{'id':50,'creator':{'id':3,'name':'Frontier'}},{'id':51,'creator':{'id':3,'name':'Frontier'}}],
            'objectPreviews':[{'creatorId':4,'creatorName':'Retained','source':'TRIBE'},{'creatorId':5,'creatorName':'Store','source':'PURCHASE'}]}
        with patch.object(client,'metadata',side_effect=lambda path:values[path.rsplit('/',1)[-1]]):groups=client.groups()
        self.assertEqual({g['id'] for g in groups},{1,2,3,4});self.assertEqual(len(groups),4)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.store=SubscriptionStore(self.root);self.manager=MMFManager(self.store)
        self.manager.put('creators',[{'id':1,'name':'Example Creator'}])
        self.item={'object_id':22,'archive_id':33,'size':3,'updated_at':'v1','filename':'January2025_Model.zip','creator':'Example Creator','creator_id':1,'folder':'Example Creator','month':'2025-01','release':'January 2025','object_name':'Model','start_month':''}
        self.item['key']=version_key(self.item)
    def test_mmf_check_interval_is_independent_and_download_claims_notification(self):
        config=self.store.config()
        self.store.atomic_write(self.store.config_path,{**config,'availability_interval_hours':12,'mmf_availability_interval_hours':1})
        self.manager.put('checked_at',1000)
        self.manager.put('settings',{'revision':1,'subscriptions':[{'id':1,'name':'Example Creator','folder':'Example Creator','start_month':''}]})
        self.manager.session.touch()
        state=self.manager.state();self.assertEqual(state['next_check_at'],4600);self.assertEqual(state['interval_hours'],1)
        with patch('app.mmf_manager.time.time',return_value=5000),patch('app.mmf_manager.subprocess.Popen') as launch:
            self.assertTrue(self.manager.start('check',due=True)['started'])
            self.assertFalse(self.manager.start('check',due=True)['started'])
            self.assertEqual(launch.call_count,1)
        self.manager.put('items',[self.item])
        with patch('app.mmf_manager.subprocess.Popen'):self.manager.start('download')
        self.assertTrue(self.manager.state()['availability_claimed'])
        self.manager.put('checked_at',6000)
        self.assertFalse(self.manager.state()['availability_claimed'])

    def test_existing_artist_prefixed_release_is_used_for_mmf(self):
        from app.mmf_manager import releases
        existing=self.root/'Example Creator'/'Example Creator 2025-01';existing.mkdir(parents=True)
        item=dict(self.item)
        releases([item],self.root)
        self.assertEqual(item['release_folder'],'Example Creator 2025-01')
        new_month={**self.item,'release':'February 2025'}
        releases([new_month],self.root)
        self.assertEqual(new_month['release_folder'],'Example Creator 2025-02')
        welcome={**self.item,'release':'Welcome Pack'}
        releases([welcome],self.root)
        self.assertEqual(welcome['release_folder'],'Example Creator Welcome Pack')

    def test_months_and_loyalty(self):
        self.assertEqual(month_for('August2025Reward.zip','1 Year Rewards'),'2025-08')
        self.assertEqual(month_for('model.zip','September 2026'),'2026-09')
        self.assertIsNone(month_for('model.zip','1 Year Rewards'))
        self.assertIsNone(month_for('model.zip','January February 2025'))
    def test_version_changes(self):
        self.assertNotEqual(version_key(self.item),version_key({**self.item,'updated_at':'v2'}))
        self.assertNotEqual(version_key(self.item),version_key({**self.item,'size':4}))
    def test_blank_folder_defaults_to_mmf_artist_name(self):
        with patch.object(self.manager,'state',return_value={}):
            self.manager.save({'revision':0,'subscriptions':[{'id':1,'name':'Wrong name','folder':'','start_month':''}]})
            self.assertEqual(self.manager.get('settings')['subscriptions'][0]['folder'],'Example Creator')
            self.manager.save({'revision':1,'subscriptions':[{'id':1,'folder':'Custom folder','start_month':''}]})
            self.assertEqual(self.manager.get('settings')['subscriptions'][0]['folder'],'Custom folder')

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
            def open(self,path):return io.BytesIO(b"<html></html>")
            def groups(self):return [{'id':1,'name':'Example Creator'}]
            def metadata(self,path):
                return [{'originalId':22,'creatorId':1,'source':'USER_GROUP','type':'object','release':44,'name':'Old loyalty collection'}] if 'objectPreviews' in path else [{'id':44,'label':'1 Year Rewards'}]
            def downloadables(self,oid):return {'archives':[{'id':33,'name':'January2025Reward.zip','size':'3','updatedAt':'v1'}]}
            def save(self):pass
        with patch.object(self.manager,'client',return_value=Client()):self.manager.check()
        self.assertEqual(len(self.manager.get('items')),1);self.assertEqual(self.manager.get('items')[0]['month'],'2025-01')
        self.assertFalse(self.manager.completed());self.assertFalse((self.manager.directory/'staging').exists())
    def test_downloaded_old_month_stays_in_scope_for_late_additions(self):
        self.manager.put('settings',{'revision':0,'subscriptions':[{'id':1,'name':'Example Creator','folder':'Example Creator','start_month':'2026-09'}]})
        with self.manager.db() as db:db.execute('INSERT INTO completed VALUES (?,?)',(self.item['key'],json.dumps(self.item)))
        class Client:
            def open(self,path):return io.BytesIO(b"<html></html>")
            def groups(self):return [{'id':1,'name':'Example Creator'}]
            def metadata(self,path):
                return [{'originalId':22,'creatorId':1,'source':'USER_GROUP','type':'object','release':44,'name':'Old model'}] if 'objectPreviews' in path else [{'id':44,'label':'January 2025'}]
            def downloadables(self,oid):return {'archives':[{'id':99,'name':'supported.zip','size':'9','updatedAt':'v2'}]}
            def save(self):pass
        with patch.object(self.manager,'client',return_value=Client()):self.manager.check()
        self.assertEqual([i['archive_id'] for i in self.manager.get('items')],[99])

    def test_successful_delivery_then_failure_continues(self):
        import zipfile
        source=self.root/'source.zip'
        with zipfile.ZipFile(source,'w') as z:z.writestr('model.stl','solid')
        first={**self.item,'size':source.stat().st_size};first['key']=version_key(first)
        bad={**first,'object_id':23,'archive_id':34,'release':'Another release'};bad['key']=version_key(bad)
        self.manager.put('queue',[bad,first]);self.manager.put('run_base',str(self.root))
        class Client:
            def open(self,path):return io.BytesIO(b"<html></html>")
            def downloadables(self,oid):
                if oid==23:raise MMFError('MMF connection failed or timed out. Try again later.')
                return {'archives':[{'id':33,'size':first['size'],'updatedAt':'v1'}]}
            def download(self,item,path,*args):path.write_bytes(source.read_bytes())
            def save(self):pass
        def delivery(path,*args,**kwargs):return self.root/'delivered.zip',path.stat().st_size,'verified-checksum'
        with patch.object(self.manager,'client',return_value=Client()),patch('app.mmf_manager.extract_images',return_value=[]),patch('app.mmf_manager.deliver',side_effect=delivery):self.manager.download()
        self.assertIn(first['key'],self.manager.completed());self.assertNotIn(bad['key'],self.manager.completed())
        self.assertEqual(len(self.manager.get('job')['errors']),1)
        self.assertEqual(self.manager.get('job')['phase'],'error')
        self.assertIn('run incomplete',self.manager.get('job')['message'])
        self.assertIn('Resume',self.manager.get('job')['message'])
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
            def open(self,path):return io.BytesIO(b"<html></html>")
            calls=0
            def downloadables(self,oid):return {'archives':[{'id':33,'size':item['size'],'updatedAt':'v1'}]}
            def download(self,item,path,*args):self.calls+=1;path.write_bytes(archive.read_bytes())
            def save(self):pass
        client=Client()
        with patch.object(self.manager,'client',return_value=client):
            self.manager.download();self.manager.download()
        self.assertEqual(client.calls,1)
        self.assertIn(item['key'],self.manager.completed())
        images=list((self.root/'Example Creator'/'Example Creator 2025-01'/'release_images').iterdir())
        self.assertEqual(len(images),1);self.assertTrue(images[0].is_file())
        self.assertEqual(len(list((self.root/'Example Creator'/'Example Creator 2025-01'/'MMF sources').rglob('*.zip'))),1)
        self.assertFalse(list((self.manager.directory/'staging').glob('**/*.zip')))
        with self.manager.db() as db:record=json.loads(db.execute('SELECT data FROM completed').fetchone()[0])
        self.assertEqual(record['image_count'],1);self.assertTrue(record['images_extracted']);self.assertEqual(len(record['sha256']),64)
        self.manager.put('settings',{'revision':1,'subscriptions':[{'id':1,'name':'Example Creator','folder':'Example Creator','start_month':''}]})
        self.manager.session.touch();self.manager.put('items',[item])
        self.store.atomic_write(self.store.config_path,{**self.store.config(),'download_directory':str(self.root)})
        output=Path(record['destination']);self.assertEqual(output.name,item['filename'])
        collage=output.parent.parent/'Example Creator-2025-01.jpg';collage.write_bytes(b'keep collage')
        output.unlink()
        state=self.manager.state();self.assertEqual(state['items'][0]['missing_files'],[output.name])
        with patch('app.mmf_manager.subprocess.Popen'):
            with self.assertRaises(MMFError):self.manager.start('redownload',release='0'*64)
            self.manager.start('redownload',release=record['release_key'])
        run=self.manager.get('redownload');self.assertEqual(run['keys'],[item['key']])
        self.assertIn(item['key'],self.manager.completed())
        self.assertTrue(self.manager.state()['resumable'])
        self.assertEqual(len(self.manager.get('redownload_history:'+run['id'])),1)
        with patch.object(self.manager,'client',return_value=client),patch('app.mmf_manager.repack',side_effect=AssertionError('Re-download must not pack')):self.manager.download();self.manager.download()
        self.assertEqual(client.calls,2)
        self.assertEqual(self.manager.get('job')['errors'],[])
        self.assertEqual(self.manager.pending_redownload_keys(),set())
        self.assertEqual(self.manager.state()['items'][0]['missing_files'],[])
        self.assertTrue(output.exists())
        with self.manager.db() as db:original=json.loads(db.execute('SELECT data FROM completed').fetchone()[0])
        self.assertEqual(original['repack']['kind'],'original_archives');self.assertEqual(Path(original['destination']).name,item['filename']);self.assertEqual(Path(original['destination']).read_bytes(),archive.read_bytes())
        self.assertTrue(images[0].is_file());self.assertEqual(collage.read_bytes(),b'keep collage')
        from app.mmf_release_prepare import execute
        release_dir=self.root/'Example Creator'/'Example Creator 2025-01'
        Image.new('RGB',(100,100),'blue').save(release_dir/'Example Creator-2025-01.jpg')
        plan={'id':'test-package','release_key':original['release_key'],'number':0,'title':'Example Creator 2025-01','keys':[item['key']],'items':[original],'base':str(self.root),'folder':'Example Creator','release_folder':'Example Creator 2025-01'}
        with patch('app.mmf_release_prepare.fetch_images',side_effect=AssertionError('Gallery downloads must be manual')):
            execute(self.manager,plan)
        self.assertEqual(plan['state'],'complete')
        self.assertEqual(Path(plan['outputs'][0]['path']).name,'Example Creator 2025-01.7z')
        self.assertTrue(Path(original['destination']).exists())




class ReleasePackagingTests(MMFTests):
    def test_same_model_can_have_different_archives_with_the_same_filename(self):
        import zipfile
        sources={};items=[]
        for aid in (41,42):
            path=self.root/f'{aid}.zip'
            with zipfile.ZipFile(path,'w') as z:z.writestr('model.stl',str(aid))
            sources[aid]=path
            item={**self.item,'archive_id':aid,'filename':'original.zip','size':path.stat().st_size}
            item['key']=version_key(item);items.append(item)
        self.manager.put('queue',items);self.manager.put('run_base',str(self.root))
        class Client:
            def downloadables(self,oid):return {'archives':[{'id':aid,'size':p.stat().st_size,'updatedAt':'v1'} for aid,p in sources.items()]}
            def download(self,item,path,*args):path.write_bytes(sources[item['archive_id']].read_bytes())
            def save(self):pass
        with patch.object(self.manager,'client',return_value=Client()):self.manager.download()
        self.assertEqual(self.manager.get('job')['errors'],[])
        self.assertEqual(self.manager.completed(),{i['key'] for i in items})
        with self.manager.db() as db:records=[json.loads(row[0]) for row in db.execute('SELECT data FROM completed')]
        self.assertEqual(len({r['destination'] for r in records}),2)
        for r in records:self.assertEqual(Path(r['destination']).read_bytes(),sources[r['archive_id']].read_bytes())

    def test_welcome_pack_combines_models_and_resumes(self):
        import zipfile
        from app.release_images import listing, command
        items=[];sources={}
        for oid,body in [(101,'first model'),(102,'second model')]:
            source=self.root/f'{oid}.zip'
            with zipfile.ZipFile(source,'w') as z:z.writestr('model.stl',body)
            item={**self.item,'object_id':oid,'object_name':f'Model {oid}','archive_id':oid,'filename':'original.zip','release_id':'welcome','release':'Welcome Pack','month':None,'size':source.stat().st_size}
            item['key']=version_key(item);items.append(item);sources[oid]=source
        self.manager.put('items',items);self.assertEqual(self.manager.state()['counts']['available'],2)
        self.manager.put('queue',items);self.manager.put('run_base',str(self.root))
        class Client:
            def open(self,path):return io.BytesIO(b"<html></html>")
            calls=0
            def downloadables(self,oid):return {'archives':[{'id':oid,'size':sources[oid].stat().st_size,'updatedAt':'v1'}]}
            def download(self,item,path,*args):self.calls+=1;path.write_bytes(sources[item['object_id']].read_bytes())
            def save(self):pass
        client=Client()
        with patch.object(self.manager,'client',return_value=client),patch('app.mmf_images.fetch_images',side_effect=AssertionError('No automatic gallery fetch')),patch('app.mmf_manager.repack',side_effect=AssertionError('No packing while downloading')):self.manager.download();self.manager.download()
        self.assertEqual(self.manager.get('job')['errors'],[])
        self.assertEqual(client.calls,2);self.assertEqual(self.manager.completed(),{i['key'] for i in items})
        destination=self.root/'Example Creator'/'Example Creator Welcome Pack'
        originals=list((destination/'MMF sources').rglob('*.zip'))
        self.assertEqual(len(originals),2)
        self.assertTrue((destination/'MMF sources'/'original.zip').is_file())
        self.assertTrue((destination/'MMF sources'/'Model 102'/'original.zip').is_file())
        self.assertFalse((destination/'MMF sources'/'originals').exists())
        self.assertEqual({p.read_bytes() for p in originals},{p.read_bytes() for p in sources.values()})
        self.assertFalse(list(destination.rglob('*.7z')))
        self.assertTrue(all(i['images_checked'] for i in self.manager.state()['items']))

    def test_named_delivery_rejects_traversal_and_keeps_month_validation(self):
        from app.file_delivery import deliver, DeliveryError
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
        with patch.object(self.store,'config',return_value={'download_directory':str(self.root)}),patch('app.mmf_manager.subprocess.Popen') as spawn:
            self.manager.start('download')
            self.assertTrue(spawn.called)
        self.assertEqual({i['key'] for i in self.manager.get('queue')},{first['key'],added['key']})

    def test_release_identity_does_not_follow_member_dates(self):
        from app.mmf_manager import releases
        first={**self.item,'release_id':'welcome','release':'Welcome Pack','month':'2023-01'}
        second={**first,'archive_id':34,'month':'2026-09'}
        monthly={**first,'release_id':'monthly','release':'September 2026'}
        groups=releases([first,second,monthly])
        self.assertEqual(sorted(map(len,groups.values())),[1,2])
        self.assertEqual(first['release_folder'],'Example Creator Welcome Pack')
        self.assertEqual(monthly['release_folder'],'Example Creator 2026-09')

class ReleaseDateTests(unittest.TestCase):
    def test_creation_date_and_special_names(self):
        from app.mmf_manager import release_date_month
        self.assertEqual(release_date_month('Balius Battle Strider','2023-09-15T16:52:43+00:00'),('2023-09','created_at'))
        self.assertEqual(release_date_month('January 2025','2026-09-01T00:00:00Z'),('2025-01','name'))
        for name in ('Welcome Pack','Loyalty Rewards (Patreon)','5 months - Gnom','1 Year Rewards'):
            self.assertEqual(release_date_month(name,'2023-10-01T00:00:00Z'),(None,None))
        for date in (None,'bad','2023-02-30'):
            self.assertEqual(release_date_month('Named release',date),(None,None))

    def test_same_month_combines_source_releases_without_using_update_date(self):
        from app.mmf_manager import releases
        items=[{'creator_id':1,'release_id':str(n),'release':name,'release_created_at':'2023-09-15T00:00:00Z','updated_at':'2026-09-10'} for n,name in enumerate(('First model','Second model'))]
        groups=releases(items)
        self.assertEqual(len(groups),1)
        self.assertEqual([i['release_folder'] for i in items],['2023-09','2023-09'])
        self.assertEqual(items[0]['release_key'],items[1]['release_key'])
        self.assertTrue(all(i['release_display_name']=='2023-09' for i in items))
        self.assertTrue(all(i['release_month']=='2023-09' for i in items))

if __name__=='__main__':unittest.main()

class SourceLayoutTests(unittest.TestCase):
    def test_duplicate_names_are_separate_units_and_only_conflicts_need_folders(self):
        from app.mmf_source_layout import source_units, source_directory
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'staging';source.mkdir();dest=root/'MMF sources';dest.mkdir()
            items=[];paths={}
            for aid,body in [(1,b'first'),(2,b'second'),(3,b'first')]:
                folder=source/str(aid);folder.mkdir();p=folder/'model.zip';p.write_bytes(body)
                item={'key':str(aid),'object_id':10,'archive_id':aid,'object_name':'Readable Model','filename':'model.zip'}
                items.append(item);paths[item['key']]=p
            units=source_units(items);self.assertEqual(len(units),3)
            reserved={}
            self.assertEqual(source_directory(dest,units[0],paths,reserved),dest)
            self.assertEqual(source_directory(dest,units[1],paths,reserved),dest/'Readable Model')
            self.assertEqual(source_directory(dest,units[2],paths,reserved),dest)
            parts=[{**items[0],'key':str(n),'filename':f'model.7z.{n:03}'} for n in (1,2)]
            self.assertEqual(len(source_units(parts)),1)

    def test_existing_sources_move_and_all_database_paths_follow(self):
        from app.mmf_source_layout import migrate_legacy_sources
        from app.file_delivery import digest
        from app.subscription_store import SubscriptionStore
        from app.mmf_manager import MMFManager
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);manager=MMFManager(SubscriptionStore(root));base=root/'downloads';base.mkdir()
            manager.store.atomic_write(manager.store.config_path,{'download_directory':str(base)})
            old=base/'Artist'/'Artist 2026-09'/'MMF sources'/'originals'/'aaaaaaaaaaaaaaaa'
            old.mkdir(parents=True);archive=old/'Original.zip';archive.write_bytes(b'original contents')
            receipt={'path':str(archive),'size':archive.stat().st_size,'sha256':digest(archive)}
            record={'key':'A','object_name':'Model','destination':str(archive),'repack':{'outputs':[receipt]}}
            with manager.db() as db:db.execute('INSERT INTO completed VALUES (?,?)',('A',json.dumps(record)))
            manager.put('old_receipt',record)
            preview=migrate_legacy_sources(manager);self.assertFalse(preview['complete']);self.assertTrue(archive.exists())
            result=migrate_legacy_sources(manager,True);target=old.parent.parent/'Original.zip'
            self.assertTrue(result['complete']);self.assertEqual(target.read_bytes(),b'original contents');self.assertFalse(old.parent.exists())
            self.assertEqual(manager.get('old_receipt')['destination'],str(target))
            with manager.db() as db:changed=json.loads(db.execute('SELECT data FROM completed').fetchone()[0])
            self.assertEqual(changed['repack']['outputs'][0]['path'],str(target))
            self.assertEqual(changed['repack']['outputs'][0]['sha256'],receipt['sha256'])
            self.assertTrue((manager.directory/'source-layout-migration'/'manager-before.sqlite3').exists())
            self.assertTrue(migrate_legacy_sources(manager,True)['complete'])

class ParallelCheckTests(unittest.TestCase):
    def test_checks_four_at_a_time_without_duplicate_requests(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            client=MMFClient(Path(tmp)/'session');barrier=threading.Barrier(4);lock=threading.Lock();active=0;peak=0;calls=[]
            def fetch(oid):
                nonlocal active,peak
                with lock:active+=1;peak=max(peak,active);calls.append(oid)
                barrier.wait(timeout=3)
                with lock:active-=1
                return {'archives':[{'id':oid,'updatedAt':'fresh'}]}
            with patch.object(client,'downloadables',side_effect=fetch):
                results=dict(client.downloadables_many([*range(1,9),1,2]))
            self.assertEqual(set(results),set(range(1,9)));self.assertEqual(len(calls),8);self.assertEqual(peak,4)
            self.assertTrue(all(r['archives'][0]['updatedAt']=='fresh' for r in results.values()))

    def test_stop_does_not_schedule_the_rest_of_the_library(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            client=MMFClient(Path(tmp)/'session');stopped=threading.Event();calls=[]
            def fetch(oid):calls.append(oid);return {'archives':[]}
            with patch.object(client,'downloadables',side_effect=fetch):
                results=client.downloadables_many(range(1,1001),stopped.is_set)
                next(results);stopped.set()
                with self.assertRaisesRegex(MMFError,'stopped'):list(results)
            self.assertLessEqual(len(calls),4)

    def test_failure_does_not_schedule_the_rest_of_the_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            client=MMFClient(Path(tmp)/'session');calls=[]
            def fetch(oid):calls.append(oid);raise MMFError('HTTP 429')
            with patch.object(client,'downloadables',side_effect=fetch):
                with self.assertRaisesRegex(MMFError,'429'):list(client.downloadables_many(range(1,1001)))
            self.assertLessEqual(len(calls),4)
