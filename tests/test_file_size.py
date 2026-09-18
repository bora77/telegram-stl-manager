import unittest
from app.file_size import display_size, matches_size
from app.telegram_ui import TelegramUI
from unittest.mock import Mock,patch
from pathlib import Path
import tempfile

class SizeTests(unittest.TestCase):
    def test_rounded_telegram_size_is_an_estimate(self):
        expected=display_size('2158.2 MB')
        self.assertEqual(expected['bytes'],2263036723)
        self.assertTrue(matches_size(2263059360,expected))
        self.assertFalse(matches_size(2000000000,expected))
        self.assertIsNone(display_size('400 MB / 2158.2 MB'))
        self.assertIsNone(display_size('2I58.2 MB'))
    def test_size_crop_uses_top_of_filename_not_icon_inflated_center(self):
        ui=object.__new__(TelegramUI);ui.lines=Mock(return_value=[{'text':'2158.2 MB'}])
        self.assertIsNotNone(ui.document_size({'y':813,'top':800}))
        ui.lines.assert_called_once_with((623,815,160,34),psm='6')
    def test_completion_requires_closed_file_and_completed_size_label(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'file';path.write_bytes(b'x'*1024)
            expected=display_size('1.0 KB')
            ui=object.__new__(TelegramUI);ui.document_size=Mock(return_value=expected);ui.download_arrow=Mock(return_value=False)
            with patch('app.telegram_ui.subprocess.run',return_value=Mock(returncode=0)):
                self.assertFalse(ui.local_download_complete(path,{'y':800},expected))
            with patch('app.telegram_ui.subprocess.run',return_value=Mock(returncode=1)):
                self.assertTrue(ui.local_download_complete(path,{'y':800},expected))
                ui.document_size.return_value=None
                self.assertFalse(ui.local_download_complete(path,{'y':800},expected))

if __name__=='__main__':unittest.main()
