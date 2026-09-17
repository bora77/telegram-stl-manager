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

if __name__=='__main__': unittest.main()
