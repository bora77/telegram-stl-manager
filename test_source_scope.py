import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from download_history import DownloadHistory
from source_scope import load_source
from telegram_cli import TelegramCLI, CLIError
from test_support import TEST_SOURCE, configure_source


class SourceScopeTests(unittest.TestCase):
    def test_missing_or_blank_source_never_enables_content_actions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):load_source(root)
            (root / 'data').mkdir()
            for value in ({}, {'chat_id': None, 'toc_message_id': None},
                          {'chat_id': True, 'toc_message_id': 100},
                          {'chat_id': 123456789, 'toc_message_id': 0},
                          {'chat_id': 123456789, 'toc_message_id': 100, 'extra': 1}):
                (root / 'data/source.json').write_text(json.dumps(value))
                with self.subTest(value=value), self.assertRaises(ValueError):load_source(root)

    def test_history_uses_local_source_and_rejects_other_groups(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(configure_source(temp))
            history = DownloadHistory(root / 'data/history.sqlite3')
            entry = dict(source_message_id=1, creator='Example', filename='file.zip')
            row = history.register(**entry, topic_url=TEST_SOURCE.prefix + '200')
            self.assertEqual(row['source_chat_id'], str(TEST_SOURCE.chat_id))
            for url in ('https://t.me/c/999/200', TEST_SOURCE.prefix + '200/1', TEST_SOURCE.prefix + '200?other'):
                with self.subTest(url=url), self.assertRaises(ValueError):history.register(**entry, topic_url=url)
            with self.assertRaises(ValueError):history.register(**entry, topic_url=TEST_SOURCE.prefix + '200', source_chat_id=999)

    def test_cli_passes_private_source_path_and_still_requires_catalog_membership(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(configure_source(temp))
            binary = root / 'fake-cli';binary.write_text('# fixture only');binary.chmod(0o700)
            client = TelegramCLI(root=root, state=root / 'state', binary=binary)
            try:
                command = client.command('stl', 'files')
                self.assertEqual(command[command.index('--source') + 1], str(root / 'data/source.json'))
                with patch.object(client, '_run') as run:
                    with self.assertRaises(CLIError):client.list_files(TEST_SOURCE.prefix + '200')
                    run.assert_not_called()
            finally:client.close()

    def test_packager_detects_private_name_across_line_breaks_and_id(self):
        path = Path(__file__).parent / 'tools/package-release.py'
        spec = importlib.util.spec_from_file_location('release_packager', path)
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);file = root / 'README.md'
            for content in ('Private Example\nClub', 'chat=987654321'):
                file.write_text(content)
                with self.assertRaises(ValueError):module.verify(root, [Path('README.md')], {'Private Example Club', '987654321'})
            file.write_text('Configure your source locally.')
            module.verify(root, [Path('README.md')], {'Private Example Club', '987654321'})
            (root / 'templates').mkdir()
            (root / 'templates/configuration.html').write_text('<p>Generic source template</p>')
            module.verify(root, [Path('templates/configuration.html')], set())
            with self.assertRaises(ValueError):module.verify(root, [Path('configuration.html')], set())
