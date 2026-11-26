"""Launcher checks using a fake Docker CLI; no container or account access."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'support').mkdir(); (self.root/'bin').mkdir()
        (self.root/'compose.yaml').write_text('name: launcher-fixture\n')
        (self.root/'support/installation-acknowledged').write_text('fixture')
        shutil.copy2(Path(__file__).parent/'sync-launchers.sh',self.root/'support/sync-launchers.sh')
        shutil.copytree(Path(__file__).parent/'launchers',self.root/'support/launchers')
        for path in (Path(__file__).parent/'launchers').iterdir():shutil.copy2(path,self.root/path.name)
        for name in ('Start.command','Stop.command'):
            shutil.copy2(Path(__file__).parent/name, self.root/name)
        fake = self.root/'bin/docker'
        fake.write_text('#!/usr/bin/env python3\nimport os,sys,json\nwith open(os.environ["CALL_LOG"],"a") as f: f.write(json.dumps({"args":sys.argv[1:],"folder":os.getenv("STL_DOWNLOAD_DIRECTORY")})+"\\n")\n')
        fake.chmod(0o755)
        fake = self.root/'bin/xdg-open'; fake.write_text('#!/bin/sh\nexit 0\n'); fake.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.root/'bin')+':'+os.environ['PATH'],
                    'CALL_LOG': str(self.root/'calls')}
    def run_script(self, name, *args):
        return subprocess.run(['bash',str(self.root/name),*args],env=self.env,
                              stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    def calls(self):
        return [json.loads(x) for x in (self.root/'calls').read_text().splitlines()]
    def test_folder_is_literal_and_persists_for_restart_and_stop(self):
        folder=self.root/'Artist files $HOME'; folder.mkdir()
        self.assertEqual(self.run_script('Start.command',str(folder)).returncode,0)
        self.assertEqual(self.run_script('Start.command').returncode,0)
        self.assertEqual(self.run_script('Stop.command').returncode,0)
        compose=[c for c in self.calls() if 'up' in c['args'] or 'stop' in c['args']]
        self.assertEqual(len(compose),3)
        self.assertTrue(all(c['folder']==str(folder) for c in compose))
    def test_missing_destination_is_not_created_or_started(self):
        folder=self.root/'missing-share'
        self.assertNotEqual(self.run_script('Start.command',str(folder)).returncode,0)
        self.assertFalse(folder.exists())
        self.assertFalse(any('up' in c['args'] for c in self.calls()))

    def test_updated_image_is_reused_without_rebuilding_old_code(self):
        folder=self.root/'downloads';folder.mkdir()
        (self.root/'support/image.txt').write_text('telegram-stl-manager-update:'+'a'*32+'\n')
        (self.root/'support/data-volume.txt').write_text('telegram-stl-manager-data-test\n')
        self.assertEqual(self.run_script('Start.command',str(folder)).returncode,0)
        up=next(c for c in self.calls() if 'up' in c['args'])
        self.assertIn('--no-build',up['args']);self.assertNotIn('--build',up['args'])
        (self.root/'.updates/current').mkdir(parents=True)
        self.assertNotEqual(self.run_script('Start.command').returncode,0)

    def test_os_specific_install_start_and_stop_share_the_same_implementation(self):
        folder=self.root/'Downloads';folder.mkdir()
        for script in ('Install-Linux.sh','Install-macOS.command','Start-Linux.sh','Start-macOS.command'):
            self.assertEqual(self.run_script(script,str(folder)).returncode,0,script)
        for script in ('Stop-Linux.sh','Stop-macOS.command'):
            self.assertEqual(self.run_script(script).returncode,0,script)

    def test_fresh_install_reads_markdown_notice_and_requires_acknowledgment(self):
        (self.root/'support/installation-acknowledged').unlink()
        (self.root/'support/app').mkdir()
        (self.root/'support/app/USAGE-NOTICE.md').write_text('Fixture usage notice')
        folder=self.root/'downloads';folder.mkdir()
        command=['bash',str(self.root/'Install-Linux.sh'),str(folder)]
        declined=subprocess.run(command,input=b'NO\n',env=self.env,capture_output=True)
        self.assertNotEqual(declined.returncode,0)
        self.assertFalse((self.root/'support/installation-acknowledged').exists())
        accepted=subprocess.run(command,input=b'I AGREE\n',env=self.env,capture_output=True)
        self.assertEqual(accepted.returncode,0,accepted.stderr)
        self.assertIn(b'Fixture usage notice',accepted.stdout)
        self.assertTrue((self.root/'support/installation-acknowledged').exists())

if __name__=='__main__': unittest.main()
