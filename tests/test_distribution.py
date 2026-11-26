import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.file_delivery import verify_configured_mount,DeliveryError

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('package_release',ROOT/'tools/package-release.py')
package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)

class DistributionTests(unittest.TestCase):
    def test_runtime_profiles_exclude_development_and_private_files(self):
        public=set(package.public_files(ROOT))
        self.assertNotIn(Path('AGENTS.md'),public);self.assertNotIn(Path('TODO.md'),public)
        self.assertIn(Path('.githooks/pre-commit'),public)
        for profile in ('windows','docker'):
            files=set(package.deployment_files(ROOT,profile))
            for name in ('AGENTS.md','TODO.md','.gitignore','.gitattributes','tests/test_updater.py','tools/package-runtime.py','tools/preview-mmf-history.py','tools/capture-screenshots.cjs','app/telegram_ui.py','windows/import-profile.py','windows/Reset-test-install.cmd'):
                self.assertNotIn(Path(name),files)
            for name in ('ROADMAP.md','README.md','INSTALL.md','AI-INSTALL-PROMPT.md','COPYRIGHT.md','USAGE-NOTICE.md','tools/launch.py','tools/init-local.py','tools/build-catalog.py','app/updater.py'):
                self.assertIn(Path(name),files)
            self.assertFalse(any(p.suffix=='.go' for p in files))
            self.assertIn(Path('.tools/tdl-stl/src/LICENSE'),files)
        build=package.deployment_files(ROOT,'docker_build')
        self.assertTrue(any(p.suffix=='.go' for p in build))
        self.assertFalse(any(p.name.endswith('_test.go') or 'testdata' in p.parts for p in build))
    def test_private_rules_are_rejected_even_if_mistakenly_allowlisted(self):
        for name in ('AGENTS.md','TODO.md'):
            with self.assertRaisesRegex(ValueError,'Private/generated'):package.verify(ROOT,[Path(name)],set())
    def test_windows_entry_points_and_public_installer_exclude_profile_import(self):
        for action in ('Install','Start','Stop','Update','Uninstall'):
            content=(ROOT/f'windows/{action}-Windows.cmd').read_text()
            target='Start' if action=='Stop' else action
            self.assertIn('support\\'+target+'.ps1',content)
        self.assertNotIn('private-profile.json',(ROOT/'windows/Install.ps1').read_text())
        self.assertNotIn('import-profile.py',(ROOT/'windows/Install.ps1').read_text())
    def test_private_mount_guards_preserve_fail_closed_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'guards.json'
            config.write_text(json.dumps([{'path':'/mnt/example-share','fstype':'cifs','sources':['//example/STL']}]))
            with patch('app.file_delivery.MOUNT_GUARDS',config):
                verify_configured_mount(Path('/mnt/example-share/Incoming'),{'fstype':'cifs','source':'//example/STL'})
                with self.assertRaises(DeliveryError):verify_configured_mount(Path('/mnt/example-share/Incoming'),{'fstype':'ext4','source':'/dev/sda'})
                config.write_text('{broken')
                with self.assertRaises(DeliveryError):verify_configured_mount(Path('/mnt/example-share/Incoming'),{})
