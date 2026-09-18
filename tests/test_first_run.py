import importlib.util,json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from app.first_run import FirstRun, parse_toc, parse_share_path
from app.source_scope import SourceScope
from app.subscription_store import SubscriptionStore
from tests.test_support import configure_source

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
            with patch('app.file_delivery.mount_identity') as verify:
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
            with patch('app.file_delivery.mount_identity',side_effect=ValueError('Share disconnected')):
                with self.assertRaisesRegex(ValueError,'disconnected'):manager.complete()
            self.assertFalse((root/'data/setup-complete').exists())

    def test_unreadable_destination_is_reported_and_blocks_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=SubscriptionStore(configure_source(root))
            store.atomic_write(root/'data/creators.json',{'creators':[{'name':'Example'}]})
            (root/'data/setup-required').touch();(root/'data/setup-settings-reviewed').touch()
            store.save_config({'revision':0,'download_directory':str(root)})
            manager=FirstRun(store);manager.phase='connected'
            original=Path.is_dir
            def check(path):
                if path==root:raise PermissionError('Share unavailable')
                return original(path)
            with patch.object(Path,'is_dir',check):
                self.assertIn('Choose an available download folder with read and write access.',manager.missing_setup())
                with self.assertRaisesRegex(ValueError,'download folder'):manager.complete()
            self.assertFalse((root/'data/setup-complete').exists())

    def test_existing_configured_installation_is_not_relocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=SubscriptionStore(configure_source(root))
            store.atomic_write(root/'data/creators.json',{'creators':[{'name':'Example'}]})
            self.assertFalse(FirstRun(store).setup_required)

class SharePathTests(unittest.TestCase):
    def test_pasted_unc_with_trailing_separator_and_special_folder(self):
        for value in ('\\\\nas\\models\\STL\\###UPLOAD', '\\\\nas\\models\\STL\\###UPLOAD\\', '  \\\\nas\\models\\STL\\###UPLOAD\\  '):
            self.assertEqual(parse_share_path(value),['nas','models','STL','###UPLOAD'])
        self.assertEqual(parse_share_path('\\\\192.168.1.10\\Models with spaces\\'),['192.168.1.10','Models with spaces'])

    def test_invalid_or_traversing_paths_are_still_rejected(self):
        for value in ('nas/models','Z:\\models','\\\\nas','\\\\nas\\models\\..\\other','\\\\nas\\models\\\\folder','\\\\nas\\models\\bad/part',None):
            with self.subTest(value=value),self.assertRaises(ValueError):parse_share_path(value)

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
            with patch('app.first_run.CLI_STATE',root/'cli'),patch.object(manager,'command',return_value=[sys.executable,str(script)]):
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
    def test_successful_share_connection_remembers_display_fields_without_password(self):
        from types import SimpleNamespace
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=SubscriptionStore(configure_source(root))
            store.atomic_write(root/'data/creators.json',{'creators':[]})
            manager=FirstRun(store)
            payload={'path':'\\\\nas\\models\\Incoming\\','username':'user','password':'private-secret','domain':'WORKGROUP'}
            mount=root/'mount';(mount/'Incoming').mkdir(parents=True)
            reply=SimpleNamespace(returncode=0,stdout=json.dumps({'mount':str(mount)}))
            original=Path.exists
            def exists(path):return True if str(path)=='/etc/telegram-stl-managed' else original(path)
            with patch.object(Path,'exists',exists),patch.object(manager,'idle',return_value=nullcontext()),patch('app.first_run.subprocess.run',return_value=reply):
                manager.share(payload)
            remembered=json.loads((root/'data/setup-share.json').read_text())
            self.assertEqual(remembered,{'path':'\\\\nas\\models\\Incoming','username':'user','domain':'WORKGROUP'})
            self.assertNotIn('private-secret',(root/'data/setup-share.json').read_text())
            self.assertEqual(store.config()['download_directory'],str(mount/'Incoming'))

    def test_nas_resolver_uses_windows_only_when_linux_lookup_fails(self):
        from types import SimpleNamespace
        spec=importlib.util.spec_from_file_location('helper',Path(__file__).resolve().parent.parent/'windows/share-helper.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        with patch.object(m.subprocess,'run') as run:
            self.assertEqual(m.resolve_server('192.168.1.20'),'192.168.1.20');run.assert_not_called()
        with patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='192.168.1.21 STREAM nas')) as run:
            self.assertEqual(m.resolve_server('nas'),'192.168.1.21');self.assertEqual(run.call_count,1)
        with patch.object(m.subprocess,'run',side_effect=[SimpleNamespace(returncode=2,stdout=''),SimpleNamespace(returncode=0,stdout='192.168.1.22\r\n')]) as run:
            self.assertEqual(m.resolve_server('nas'),'192.168.1.22')
            self.assertTrue(run.call_args.args[0][0].endswith('powershell.exe'))
        with patch.object(m.subprocess,'run',side_effect=FileNotFoundError):
            with self.assertRaises(m.ResolutionRequired):m.resolve_server('nas')
        with patch.object(m.subprocess,'run',side_effect=m.subprocess.TimeoutExpired('lookup',12)):
            with self.assertRaises(m.ResolutionRequired):m.resolve_server('nas')
        with patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='192.168.1.22,other-option=yes')):
            with self.assertRaises(m.ResolutionRequired):m.resolve_server('nas')
        with patch.object(m.subprocess,'run') as run:
            for name in ("nas';Start-Process bad",'$(bad)','nas,ip=other'):
                with self.assertRaises(ValueError):m.resolve_server(name)
            run.assert_not_called()

    def test_nas_mount_uses_resolved_ip_but_saves_original_hostname(self):
        from types import SimpleNamespace
        spec=importlib.util.spec_from_file_location('helper',Path(__file__).resolve().parent.parent/'windows/share-helper.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(m,'DIRECTORY',root/'private'),patch.object(m,'MOUNT',root/'mount'),patch.object(m,'resolve_server',return_value='192.168.1.22'),patch.object(m.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=1000,pw_gid=1000)),patch.object(m.subprocess,'run',side_effect=[SimpleNamespace(returncode=1),SimpleNamespace(returncode=0)]) as run:
                m.connect({'server':'nas','share':'models','username':'user','password':'secret'})
                mount=run.call_args.args[0]
                self.assertIn('//nas/models',mount);self.assertIn(',ip=192.168.1.22,',mount[-1])
                self.assertEqual(json.loads((root/'private/share.json').read_text())['server'],'nas')
                self.assertEqual([p.name for p in (root/'private').iterdir()],['share.json'])

    def test_share_errors_are_actionable_without_echoing_private_output(self):
        spec=importlib.util.spec_from_file_location('helper',Path(__file__).resolve().parent.parent/'windows/share-helper.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        cases=[(b'could not resolve address for secret-host: Unknown error','hostname'),
               (b'mount error(13): Permission denied password=private-secret','access denied'),
               (b'mount error(2): No such file or directory','not found'),
               (b'mount error(110): Connection timed out','timed out'),
               (b'mount error(113): No route to host','could not be reached'),
               (b'mount error(95): Operation not supported','SMB2/SMB3'),
               (b'mount error(123): private-secret','mount error 123'),
               (b'private-secret and secret-host','without a recognized error')]
        for raw,expected in cases:
            with self.subTest(raw=raw):
                result=m.mount_failure(raw)
                self.assertIn(expected,result);self.assertNotIn('private-secret',result);self.assertNotIn('secret-host',result)

    def test_share_helper_rejects_option_and_credential_injection(self):
        spec=importlib.util.spec_from_file_location('helper',Path(__file__).resolve().parent.parent/'windows/share-helper.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        good={'server':'nas.example','share':'STL Files','username':'user','password':'secret'}
        self.assertEqual(m.validate(good)['share'],'STL Files')
        for bad in ({'server':'server,-o'},{'share':'../x'},{'share':'x,uid=0'},{'username':'u\npassword=x'},{'password':'x\ndomain=bad'}):
            with self.assertRaises(ValueError):m.validate({**good,**bad})

class DownloadPreservationTests(unittest.TestCase):
    def helper(self):
        spec=importlib.util.spec_from_file_location('preserve',Path(__file__).resolve().parent.parent/'windows/preserve-downloads.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
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
        spec=importlib.util.spec_from_file_location('profile_import',Path(__file__).resolve().parent.parent/'windows/import-profile.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
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
