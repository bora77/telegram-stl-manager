import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile
from app.gumroad_client import GumroadClient, checked_url
from app.gumroad_manager import GumroadManager
from app.mmf_client import MMFError, LoginRequired
from app.mmf_manager import MMFManager
from app.subscription_store import SubscriptionStore


class Response(io.BytesIO):
    def __init__(self,body,status=200,headers=None):
        super().__init__(body);self.status=status;self.headers=headers or {};self.url='https://gumroad.com/library'


class GumroadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'data').mkdir();self.base=self.root/'downloads';self.base.mkdir()
        (self.root/'data/config.json').write_text(json.dumps({'download_directory':str(self.base)}))
        self.store=SubscriptionStore(self.root);self.manager=GumroadManager(self.store)
        self.client=GumroadClient(self.root/'session.cookies')
        self.sub={'id':'creator-opaque=','name':'Example','folder':'Example','start_month':'2026-08'}
        self.manager.put('settings',{'revision':0,'subscriptions':[self.sub]})

    def test_default_disabled_and_separate_database(self):
        self.assertFalse(self.store.config()['gumroad_beta_enabled'])
        self.assertEqual(self.store.config()['gumroad_availability_interval_hours'],4)
        self.assertNotEqual(self.manager.database,MMFManager(self.store).database)

    def test_json_login_and_verification_do_not_save_password(self):
        responses=[{'component':'Logins/New','props':{'authenticity_token':'csrf'}},
                   {'component':'TwoFactorAuthentication/Show','props':{'two_factor_method':'email'}}]
        with patch.object(self.client,'metadata',side_effect=responses) as request:
            result=self.client.login('person@example.invalid','not-a-real-password')
            self.assertTrue(result['verification_required'])
            self.assertEqual(request.call_args.kwargs['payload']['user']['login_identifier'],'person@example.invalid')
        self.assertNotIn('not-a-real-password',self.client.session.read_text())
        with patch.object(self.client,'metadata',side_effect=[{'component':'TwoFactorAuthentication/Show','props':{'user_id':'u','authenticity_token':'csrf'}},{'component':'Library/Index'}]),patch.object(self.client,'groups',return_value=[]):
            self.assertFalse(self.client.login(token='123456')['verification_required'])

    def test_wrong_password_does_not_create_session(self):
        with patch.object(self.client,'metadata',return_value={'component':'Logins/New','props':{'authenticity_token':'csrf'}}):
            with self.assertRaises(LoginRequired):self.client.login('x','bad')
        self.assertFalse(self.client.session.exists())

    def test_content_preserves_filename_and_ignores_stream_only(self):
        content={'content':{'content_items':[{'type':'folder','name':'August 2026','children':[
            {'type':'file','id':'a=','file_name':'Dragon','extension':'ZIP','file_size':3,'download_url':'/r/token/product_files?product_file_ids[]=a'},
            {'type':'file','id':'b=','file_name':'Read only','extension':'PDF','file_size':1,'download_url':None}]}]}}
        with patch.object(self.client,'content',return_value=content):
            files=self.client.downloadables('p')['archives']
        self.assertEqual(len(files),1);self.assertEqual(files[0]['name'],'Dragon.zip');self.assertEqual(files[0]['folder'],'August 2026')

    def test_pagination_collects_all_purchases_and_deduplicates(self):
        cards=[{'purchase':{'id':x}} for x in ['1','2','3']]
        with patch.object(self.client,'library',side_effect=[{'results':cards[:2],'pagination':{'pages':2}},{'results':cards[1:],'pagination':{'pages':2}}]):
            self.assertEqual(len(list(self.client.cards('creator'))),3)

    def fake_client(self, folder='Supported', name='August 2026', file_id='file='):
        client=Mock();client.post_cards.return_value=[];client.groups.return_value=[{'id':self.sub['id'],'name':'Example'}]
        client.cards.return_value=[{'purchase':{'id':'purchase=','download_url':'https://gumroad.com/d/private-token'},'product':{'name':name}}]
        client.downloadables.return_value={'archives':[{'id':file_id,'name':'original.zip','size':3,'updatedAt':'','folder':folder}]}
        return client

    def test_monthly_group_and_retroactive_new_file(self):
        client=self.fake_client()
        with patch.object(self.manager,'client',return_value=client):self.manager.check()
        item=self.manager.get('items')[0];self.assertEqual(item['release_folder'],'Example 2026-08')
        with self.manager.db() as db:db.execute('INSERT INTO completed VALUES (?,?)',(item['key'],json.dumps(item)))
        self.manager.put('settings',{'subscriptions':[{**self.sub,'start_month':'2026-09'}]})
        client=self.fake_client(file_id='new-supported-file=')
        with patch.object(self.manager,'client',return_value=client):self.manager.check()
        updated=self.manager.get('items')[0]
        self.assertEqual(updated['release_key'],item['release_key']);self.assertNotEqual(updated['key'],item['key'])
        self.assertNotIn('private-token',json.dumps(self.manager.get('items')))

    def test_named_release_does_not_use_purchase_date(self):
        with patch.object(self.manager,'client',return_value=self.fake_client(name='Welcome Pack')):self.manager.check()
        item=self.manager.get('items')[0];self.assertIsNone(item['release_month']);self.assertIn('Welcome',item['release_folder'])

    def test_membership_post_files_use_publication_month(self):
        client=self.fake_client(name='Monthly membership')
        client.post_cards.return_value=[{'purchase':{'id':'purchase=:post:one','download_url':'https://gumroad.com/d/post-token'},'product':{'name':'New models'},'published_at':'2026-09-02T12:00:00Z','is_post':True}]
        with patch.object(self.manager,'client',return_value=client):self.manager.check()
        items=self.manager.get('items')
        self.assertEqual(len(items),2)
        self.assertEqual(items[1]['release_month'],'2026-09')
        client.post_cards.assert_called_once_with('purchase=',self.manager.stopped)

    def test_failed_check_keeps_previous_results(self):
        self.manager.put('items',[{'old':True}]);self.manager.put('checked_at',123)
        client=self.fake_client();client.downloadables.side_effect=MMFError('Offline')
        with patch.object(self.manager,'client',return_value=client),self.assertRaises(MMFError):self.manager.check()
        self.assertEqual(self.manager.get('items'),[{'old':True}]);self.assertEqual(self.manager.get('checked_at'),123)

    def test_download_resume_uses_no_account_cookie_on_storage(self):
        self.client.cache={};file={'id':'f','url':'https://gumroad.com/r/token/product_files'}
        item={'object_id':'p','archive_id':'f','size':6}
        path=self.root/'archive.zip';path.with_name('archive.zip.partial').write_bytes(b'abc')
        opener=Mock();opener.open.return_value=Response(b'def',206,{'Content-Range':'bytes 3-5/6'})
        with patch.object(self.client,'downloadables',return_value={'archives':[file]}),patch.object(self.client,'metadata',return_value={'files':[{'url':'https://files.gumroad.com/download'}]}),patch('urllib.request.build_opener',return_value=opener) as factory:
            self.client.download(item,path,lambda *args:None,lambda:False)
        self.assertEqual(path.read_bytes(),b'abcdef');self.assertEqual(len(factory.call_args.args),1)
        self.assertNotIn('Cookie',opener.open.call_args.args[0].headers)

    def test_incomplete_transfer_not_recorded_complete(self):
        opener=Mock();opener.open.return_value=Response(b'a')
        with patch.object(self.client,'downloadables',return_value={'archives':[{'id':'f','url':'/r/token/product_files'}]}),patch.object(self.client,'metadata',return_value={'files':[{'url':'https://files.gumroad.com/file'}]}),patch('urllib.request.build_opener',return_value=opener):
            with self.assertRaises(MMFError):self.client.download({'object_id':'p','archive_id':'f','size':5},self.root/'file',lambda *a:None,lambda:False)
        self.assertFalse((self.root/'file').exists())

    def test_untrusted_hosts_rejected(self):
        for url in ['http://gumroad.com/file','https://localhost/file','https://127.0.0.1/file','https://gumroad.com.evil.invalid/file','https://user:secret@gumroad.com/file']:
            with self.assertRaises(MMFError):checked_url(url,files=True)

    def test_original_download_and_image_extraction(self):
        from PIL import Image
        buffer=io.BytesIO();Image.new('RGB',(10,10),'red').save(buffer,format='PNG')
        source=self.root/'original.zip'
        with zipfile.ZipFile(source,'w') as z:z.writestr('model.stl',b'model');z.writestr('preview.png',buffer.getvalue())
        client=self.fake_client();client.downloadables.return_value['archives'][0]['size']=source.stat().st_size
        with patch.object(self.manager,'client',return_value=client):self.manager.check()
        items=self.manager.get('items');self.manager.put('queue',items);self.manager.put('run_base',str(self.base))
        def download(item,path,*args):path.write_bytes(source.read_bytes())
        client.download.side_effect=download
        with patch.object(self.manager,'client',return_value=client):self.manager.download()
        job=self.manager.get('job');self.assertEqual(job['phase'],'complete',job)
        self.assertEqual(len(self.manager.completed()),1)
        release=self.base/'Example'/'Example 2026-08'
        self.assertTrue((release/'Gumroad sources'/'original.zip').is_file())
        self.assertEqual(len(list((release/'release_images').glob('*.png'))),1)

    def test_completed_files_are_not_fetched_for_addendum(self):
        client=self.fake_client()
        with patch.object(self.manager,'client',return_value=client):self.manager.check()
        first=self.manager.get('items')[0]
        second={**first,'archive_id':'new','key':'new-key','filename':'new.zip'}
        with self.manager.db() as db:db.execute('INSERT INTO completed VALUES (?,?)',(first['key'],json.dumps(first)))
        self.manager.put('queue',[first,second])
        with patch.object(MMFManager,'download') as shared:self.manager.download()
        self.assertEqual([i['archive_id'] for i in self.manager.get('queue')],['new']);shared.assert_called_once()

    def test_loose_file_can_be_packaged(self):
        from app.mmf_repack import repack
        source=self.root/'part.stl';source.write_bytes(b'solid test\nendsolid test')
        outputs=repack(source,self.root/'packed','test',compression_level=1)
        self.assertTrue(outputs[0].is_file())

if __name__=='__main__':unittest.main()
