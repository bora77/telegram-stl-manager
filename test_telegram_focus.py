from test_support import TEST_SOURCE
import subprocess
import unittest
from unittest.mock import Mock,patch
from telegram_ui import TelegramUI,UIError

class FocusTests(unittest.TestCase):
    def make_ui(self):
        ui=object.__new__(TelegramUI);ui.xdo=Mock();return ui
    def test_qt_menu_with_no_active_window_accepts_telegram_input_focus(self):
        ui=self.make_ui()
        def xdo(command):
            if command=='getactivewindow':raise subprocess.CalledProcessError(1,['xdotool',command])
            return '4203939'
        ui.xdo.side_effect=xdo
        with patch('telegram_ui.subprocess.check_output',return_value='WM_CLASS(STRING) = "Telegram", "TelegramDesktop"'):
            ui.guard()
        ui.xdo.assert_called_once_with('getwindowfocus')
    def test_other_app_focus_is_not_overridden_by_telegram_active_window(self):
        ui=self.make_ui();ui.xdo.return_value='123'
        with patch('telegram_ui.subprocess.check_output',return_value='WM_CLASS(STRING) = "terminal", "Terminal"'),self.assertRaises(UIError):ui.guard()
        ui.xdo.assert_called_once_with('getwindowfocus')
    def test_missing_window_reports_actionable_error(self):
        ui=self.make_ui();ui.xdo.side_effect=subprocess.CalledProcessError(1,['xdotool'])
        with self.assertRaisesRegex(UIError,'focus could not be verified'):ui.guard()
    def test_classless_focus_can_fall_back_to_active_telegram(self):
        ui=self.make_ui();ui.xdo.side_effect=['1','2']
        with patch('telegram_ui.subprocess.check_output',side_effect=['WM_CLASS: not found.','WM_CLASS(STRING) = "Telegram", "TelegramDesktop"']):ui.guard()
        self.assertEqual(ui.xdo.call_count,2)

class ResetTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('telegram_ui.load_source', return_value=TEST_SOURCE))

    def test_outside_group_is_rejected_before_resetting_or_navigating(self):
        ui=object.__new__(TelegramUI);ui.reset_view=Mock();ui._open_url=Mock()
        with self.assertRaises(UIError):ui.open_topic('https://t.me/c/999/123')
        ui.reset_view.assert_not_called();ui._open_url.assert_not_called()
    def test_failed_home_verification_never_opens_attachment(self):
        ui=object.__new__(TelegramUI);ui.key=Mock();ui._open_url=Mock();ui.lines=Mock(return_value=[{'text':'Unexpected view'}])
        with self.assertRaisesRegex(UIError,'Could not reset'):ui.open_topic('https://t.me/c/123456789/200/302')
        ui._open_url.assert_called_once_with('https://t.me/c/123456789/100')

if __name__=='__main__':unittest.main()
