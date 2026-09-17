import importlib.util,json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from first_run import FirstRun,parse_toc
from source_scope import SourceScope
from subscription_store import SubscriptionStore
from test_support import configure_source

class SetupCompletionTests(unittest.TestCase):
    def test_new_setup_requires_login_catalog_settings_and_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=SubscriptionStore(configure_source(root))
            store.atomic_write(root/'data/creators.json',{'creators':[]})
            manager=FirstRun(store)
            self.assertTrue(manager.setup_required)
            with self.assertRaises(ValueError):manager.complete()
            manager.phase='connected'
            store.atomic_write(root/'data/creators.json',{'creators':[{'name':'Example'}]})
            store.save_config({'revision':0,'download_directory':str(root),'download_storage':'local'})
            with self.assertRaisesRegex(ValueError,'Save configuration'):manager.complete()
            (root/'data/setup-settings-reviewed').touch()
            with patch('file_delivery.mount_identity') as verify:
                result=manager.complete();verify.assert_called_once_with(str(root))
            self.assertFalse(result['setup_required'])
            self.assertFalse(FirstRun(store).setup_required)

    def test_enabled_mmf_requires_connection_and_unavailable_folder_blocks_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=SubscriptionStore(configure_source(root))
            store.atomic_write(root/'data/creators.json',{'creators':[{'name':'Example'}]})
            (root/'data/setup-required').touch();(root/'data/setup-settings-reviewed').touch()
            store.save_config({'revision':0,'download_directory':str(root),'mmf_beta_enabled':True})
            manager=FirstRun(store);manager.phase='connected'
            with self.assertRaisesRegex(ValueError,'MyMiniFactory'):manager.complete()
            store.save_config({'revision':1,'download_directory':str(root),'mmf_beta_enabled':False})
            with patch('file_delivery.mount_identity',side_effect=ValueError('Share disconnected')):
                with self.assertRaisesRegex(ValueError,'disconnected'):manager.complete()
            self.assertFalse((root/'data/setup-complete').exists())

    def test_existing_configured_installation_is_not_relocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=SubscriptionStore(configure_source(root))
            store.atomic_write(root/'data/creators.json',{'creators':[{'name':'Example'}]})
            self.assertFalse(FirstRun(store).setup_required)

class FirstRunTests(unittest.TestCase):
    def test_toc_utf16_and_source_boundary(self):
        scope=SourceScope(123456789,10);text='😀 Artist One';name='Artist One'
        raw={'ID':10,'PeerID':{'ChannelID':scope.chat_id},'Message':text,'Entities':[{'Offset':3,'Length':len(name),'URL':scope.prefix+'20'},{'Offset':3,'Length':len(name),'URL':'https://t.me/c/999/30'}]}
        result=parse_toc({'id':scope.chat_id,'messages':[{'raw':raw}]},scope)
        self.assertEqual(result['creators'],[{'name':name,'topic_url':scope.prefix+'20','within_approved_group':True}])
        with self.assertRaises(ValueError):parse_toc({'id':999,'messages':[{'raw':raw}]},scope)
        with self.assertRaises(ValueError):parse_toc({'id':scope.chat_id,'messages':[]},scope)
    def test_toc_topic_includes_followup_posts_but_not_other_topics(self):
        scope=SourceScope(123456789,10)
        def post(mid,parent):
            return {'raw':{'ID':mid,'PeerID':{'ChannelID':scope.chat_id},'Message':'Artist','ReplyTo':{'ForumTopic':True,'ReplyToTopID':parent},'Entities':[{'Offset':0,'Length':6,'URL':scope.prefix+str(mid+100)}]}}
        result=parse_toc({'id':scope.chat_id,'messages':[post(11,10),post(12,10),post(13,99)]},scope)
        self.assertEqual(len(result['creators']),2)
        self.assertNotIn(scope.prefix+'113',[r['topic_url'] for r in result['creators']])

    def test_login_prompts_without_exposing_responses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=SubscriptionStore(configure_source(root));store.atomic_write(root/'data/creators.json',{'creators':[]})
            script=root/'fake-login.py';script.write_text("import sys\nfor p in ['Enter your phone number:','Enter Code:','Enter 2FA Password:']:\n print(p,flush=True)\n value=input()\n if not value:sys.exit(1)\n")
            manager=FirstRun(store)
            import sys
            with patch('first_run.CLI_STATE',root/'cli'),patch.object(manager,'command',return_value=[sys.executable,str(script)]):
                manager.start_login()
                for phase in ('phone','code','password'):
                    deadline=time.monotonic()+5
                    while manager.phase!=phase and time.monotonic()<deadline:time.sleep(.02)
                    self.assertEqual(manager.phase,phase)
                    with self.assertRaises(ValueError):manager.answer({'challenge':-1,'value':'test'})
                    manager.answer({'challenge':manager.challenge,'value':'private-test-value'})
                    self.assertNotIn('private-test-value',json.dumps(manager.state()))
                deadline=time.monotonic()+5
                while manager.phase!='connected' and time.monotonic()<deadline:time.sleep(.02)
                self.assertEqual(manager.phase,'connected')
                self.assertTrue((root/'data/telegram-connected').exists())
    def test_share_helper_rejects_option_and_credential_injection(self):
        spec=importlib.util.spec_from_file_location('helper',Path(__file__).parent/'windows/share-helper.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        good={'server':'nas.example','share':'STL Files','username':'user','password':'secret'}
        self.assertEqual(m.validate(good)['share'],'STL Files')
        for bad in ({'server':'server,-o'},{'share':'../x'},{'share':'x,uid=0'},{'username':'u\npassword=x'},{'password':'x\ndomain=bad'}):
            with self.assertRaises(ValueError):m.validate({**good,**bad})

class DownloadPreservationTests(unittest.TestCase):
    def helper(self):
        spec=importlib.util.spec_from_file_location('preserve',Path(__file__).parent/'windows/preserve-downloads.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
    def test_finished_downloads_copied_and_verified_without_removing_source(self):
        m=self.helper()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'downloads';source.mkdir();(source/'release').mkdir();(source/'release/archive.7z').write_bytes(b'original')
            target=root/'Windows backup';result=m.preserve(source,target)
            self.assertEqual(result['preserved_files'],1)
            self.assertEqual((source/'release/archive.7z').read_bytes(),b'original')
            self.assertEqual((target/'release/archive.7z').read_bytes(),b'original')
            with self.assertRaises(ValueError):m.preserve(source,target)
    def test_external_destination_never_backed_up_or_traversed(self):
        m=self.helper()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir();nas=root/'nas';nas.mkdir();(root/'data/settings.json').write_text(json.dumps({'download_directory':str(nas)}))
            with patch.object(m.subprocess,'check_output',return_value='cifs\n'):
                self.assertIsNone(m.destination(root))

    def test_disconnected_managed_share_is_not_inspected(self):
        m=self.helper()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            (root/'data/settings.json').write_text(json.dumps({'download_directory':'/mnt/managed-share/Incoming'}))
            original=Path.exists
            def exists(path):
                if path.is_relative_to('/mnt/managed-share'):raise PermissionError('Disconnected root-owned mount')
                return original(path)
            with patch.object(Path,'exists',exists),patch.object(m.subprocess,'check_output',side_effect=AssertionError('Must not probe remote storage')):
                self.assertIsNone(m.destination(root,Path('/mnt/managed-share')))

class PrivateProfileTests(unittest.TestCase):
    def helper(self):
        spec=importlib.util.spec_from_file_location('profile_import',Path(__file__).parent/'windows/import-profile.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
    def test_private_import_preserves_history_session_and_is_once_only(self):
        import base64,sqlite3
        m=self.helper()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'app';home=Path(tmp)/'home';(root/'data').mkdir(parents=True)
            (root/'data/creators.json').write_text('{"creators":[]}')
            dbpath=Path(tmp)/'history';db=sqlite3.connect(dbpath);db.execute('CREATE TABLE proof(value)');db.execute('INSERT INTO proof VALUES (42)');db.commit();db.close()
            profile={'files':{'source.json':{'chat_id':123456789,'toc_message_id':10},'creators.json':{'creators':[]},'settings.json':{'download_directory':'/mnt/test-share/Incoming'}},'blobs':{'telegram-session':base64.b64encode(b'private session').decode(),'download-history.sqlite3':base64.b64encode(dbpath.read_bytes()).decode()}}
            self.assertTrue(m.install(root,profile,home))
            self.assertFalse(m.install(root,profile,home))
            self.assertEqual((home/'.local/share/telegram-stl-tdl/data/telegram-stl-trial').read_bytes(),b'private session')
            with sqlite3.connect(root/'data/download-history.sqlite3') as saved:self.assertEqual(saved.execute('SELECT value FROM proof').fetchone()[0],42)
            self.assertTrue((root/'data/telegram-connected').exists())
            self.assertEqual((root/'data/download-history.sqlite3').stat().st_mode & 0o777,0o600)
    def test_bad_profile_rejected_before_mutation(self):
        m=self.helper()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir();(root/'data/creators.json').write_text('{"creators":[]}')
            with self.assertRaises(ValueError):m.install(root,{'blobs':{'../escape':'AA=='}})
            self.assertFalse((root/'data/source.json').exists())
