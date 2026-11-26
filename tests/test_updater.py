import io,json,tempfile,unittest,urllib.request
from pathlib import Path
from unittest.mock import patch
from app.updater import Updater,GitHubRedirect,version,ASSET

class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'VERSION').write_text('0.1.12')
        self.updater=Updater(self.root)
    def reply(self,tag='v0.1.13',digest='sha256:'+'a'*64):
        return io.BytesIO(json.dumps({'tag_name':tag,'assets':[{'name':ASSET,'digest':digest,'size':20,'url':'https://api.github.com/repos/bora77/telegram-stl-manager/releases/assets/123'}]}).encode())
    def test_check_version_and_digest(self):
        with patch.object(self.updater,'request',side_effect=[self.reply(),io.BytesIO(b'[]')]):self.updater.check()
        self.assertTrue(self.updater.status()['available'])
        self.assertEqual(self.updater.release['sha256'],'a'*64)
        with patch.object(self.updater,'request',return_value=self.reply('v0.1.11')):self.updater.check()
        self.assertFalse(self.updater.status()['available'])
    def test_reject_missing_digest(self):
        with patch.object(self.updater,'request',return_value=self.reply(digest=None)),self.assertRaises(ValueError):self.updater.check()
        self.assertIsNone(self.updater.release)
    def test_settings_never_return_access_token(self):
        state=self.updater.save({'repository':'example/private','token':'private-secret'})
        self.assertNotIn('private-secret',json.dumps(state));self.assertTrue(state['has_token'])
        self.assertEqual((self.updater.directory/'settings.json').stat().st_mode & 0o777,0o600)
    def test_redirect_removes_token(self):
        req=urllib.request.Request('https://api.github.com/repos/a/b/releases/assets/1',headers={'Authorization':'Bearer secret'})
        redirected=GitHubRedirect().redirect_request(req,None,302,'',{},'https://release-assets.githubusercontent.com/asset')
        self.assertIsNone(redirected.get_header('Authorization'))
        with self.assertRaises(ValueError):GitHubRedirect().redirect_request(req,None,302,'',{},'http://example.com/file')
    def test_corrupt_download_never_launches_installer(self):
        self.updater.release={'version':'0.1.13','url':'https://api.github.com/asset','size':4,'sha256':'0'*64}
        with patch.object(self.updater,'request',return_value=io.BytesIO(b'bad!')),patch('app.updater.subprocess.run') as run,self.assertRaisesRegex(ValueError,'checksum'):
            self.updater.install()
        run.assert_not_called();self.assertFalse((self.updater.directory/'package.partial').exists())
    def test_pending_install_blocks_until_confirmation_marker_removed(self):
        (self.root/'data/update-installing').touch()
        updater=Updater(self.root)
        self.assertTrue(updater.status()['reserved'])
        (self.root/'data/update-installing').unlink()
        self.assertFalse(updater.status()['reserved'])
    def test_version_sorting_is_numeric(self):
        self.assertGreater(version('0.1.13'),version('0.1.9'))
        with self.assertRaises(ValueError):version('../version')

    def test_docker_guard_waits_and_requires_matching_host_token(self):
        with patch.dict('os.environ',{'TELEGRAM_STL_DOCKER':'1'}):
            self.updater.busy=lambda:True
            self.assertTrue(self.updater.docker_guard({'action':'reserve','token':'a'*32})['busy'])
            self.assertTrue(self.updater.status()['reserved'])
            with self.assertRaises(ValueError):self.updater.docker_guard({'action':'release','token':'b'*32})
            restarted=Updater(self.root)
            self.assertTrue(restarted.status()['reserved'])
            self.assertFalse(restarted.docker_guard({'action':'release','token':'a'*32})['reserved'])
            self.assertFalse(restarted.docker_guard({'action':'release','token':'a'*32})['reserved'])
        with patch.dict('os.environ',{'TELEGRAM_STL_DOCKER':''}),self.assertRaises(ValueError):self.updater.docker_guard({'action':'reserve','token':'a'*32})

    def test_changelog_contains_all_newer_stable_versions(self):
        history=[{'tag_name':'v0.1.13','body':'Latest'},{'tag_name':'v0.1.12','body':'Installed'},{'tag_name':'v0.1.14','body':'Preview','prerelease':True}]
        (self.root/'VERSION').write_text('0.1.10')
        with patch.object(self.updater,'request',side_effect=[self.reply(),io.BytesIO(json.dumps(history).encode())]):self.updater.check()
        self.assertEqual([r['version'] for r in self.updater.status()['changes']],['0.1.13','0.1.12'])
