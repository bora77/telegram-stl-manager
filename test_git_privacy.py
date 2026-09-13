"""Git checks run in disposable repositories with synthetic private source IDs."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_support import configure_source, TEST_SOURCE

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('git_privacy', ROOT/'tools/check-git.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class GitPrivacyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name);configure_source(self.root)
        self.git('init', '-q', '-b', 'main')
        self.manifest = {'files':['distribution.json', 'public.txt'], 'trees':[]}
        self.write_manifest()
        (self.root/'public.txt').write_text('Public application source.\n')
        self.git('add', '--', 'distribution.json', 'public.txt')

    def git(self, *args, check=True):
        return subprocess.run(['git', '-C', str(self.root), *args], check=check,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def write_manifest(self):
        (self.root/'distribution.json').write_text(json.dumps(self.manifest))

    def test_clean_index_ignores_unstaged_private_content(self):
        (self.root/'public.txt').write_text(str(TEST_SOURCE.chat_id))
        self.assertEqual(checker.verify_index(self.root), 2)

    def test_clean_working_copy_cannot_hide_private_content_in_the_index(self):
        (self.root/'public.txt').write_text(str(TEST_SOURCE.chat_id))
        self.git('add', '--', 'public.txt')
        (self.root/'public.txt').write_text('This unstaged copy is clean.\n')
        with self.assertRaisesRegex(ValueError, 'Private source reference'):
            checker.verify_index(self.root)

    def test_private_paths_stay_blocked_even_if_added_to_the_manifest(self):
        private = self.root/'data/notes.txt';private.write_text('Local notes')
        self.manifest['files'].append('data/notes.txt');self.write_manifest()
        self.git('add', '--', 'distribution.json', 'data/notes.txt')
        with self.assertRaisesRegex(ValueError, 'Private/generated content'):
            checker.verify_index(self.root)

    def test_only_the_staged_manifest_can_allow_new_files(self):
        (self.root/'extra.txt').write_text('An additional public source')
        self.manifest['files'].append('extra.txt');self.write_manifest()
        self.git('add', '--', 'extra.txt')
        with self.assertRaisesRegex(ValueError, 'outside the public distribution'):
            checker.verify_index(self.root)
        self.git('add', '--', 'distribution.json')
        self.assertEqual(checker.verify_index(self.root), 3)

    def test_symlinks_and_missing_required_sources_are_rejected(self):
        (self.root/'public.txt').unlink()
        (self.root/'public.txt').symlink_to('data/source.json')
        self.git('add', '--', 'public.txt')
        with self.assertRaisesRegex(ValueError, 'Only regular public source files'):
            checker.verify_index(self.root)
        (self.root/'public.txt').unlink();self.git('add', '-u')
        with self.assertRaisesRegex(ValueError, 'Required public files are missing'):
            checker.verify_index(self.root)

    def test_installed_hook_blocks_bad_commit_then_allows_clean_commit(self):
        (self.root/'tools').mkdir();(self.root/'.githooks').mkdir()
        for name in ('check-git.py', 'package-release.py'):
            shutil.copy2(ROOT/'tools'/name, self.root/'tools'/name)
        shutil.copy2(ROOT/'.githooks/pre-commit', self.root/'.githooks/pre-commit')
        self.manifest['files'].append('.githooks/pre-commit')
        self.manifest['trees'].append('tools');self.write_manifest()
        (self.root/'public.txt').write_text(str(TEST_SOURCE.chat_id))
        self.git('add', '--', 'distribution.json', 'public.txt', 'tools', '.githooks')
        self.git('config', '--local', 'core.hooksPath', '.githooks')
        commit = ('-c', 'user.name=Test Author', '-c', 'user.email=test@example.invalid',
                  '-c', 'commit.gpgsign=false', 'commit', '-m', 'Fixture commit')
        rejected = self.git(*commit, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn(b'Git privacy check failed', rejected.stderr)
        self.assertNotEqual(self.git('rev-parse', '--verify', 'HEAD', check=False).returncode, 0)
        (self.root/'public.txt').write_text('Public application source.\n')
        self.git('add', '--', 'public.txt');self.git(*commit)
        self.assertEqual(self.git('status', '--porcelain', '--untracked-files=no').stdout, b'')


if __name__ == '__main__':unittest.main()
