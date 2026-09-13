from test_support import TEST_SOURCE
import unittest
from unittest.mock import Mock, patch

from telegram_ui import TelegramUI, UIError, attachment_name_matches, attachment_link


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('telegram_ui.load_source', return_value=TEST_SOURCE))

    @patch('telegram_ui.time.sleep')
    def test_download_waits_for_copy_link_toast_to_clear(self,_):
        ui=object.__new__(TelegramUI);ui.xdo=Mock()
        ui.download_arrow=Mock(side_effect=[False,True,True])
        ui.document_size=Mock(side_effect=[None,{'bytes':4194304000}])
        self.assertEqual(ui.ready_download_control({'y':430},lambda:False)['bytes'],4194304000)
        self.assertEqual(ui.download_arrow.call_count,3)

    @patch('telegram_ui.time.monotonic',side_effect=[0,7])
    def test_loaded_or_obscured_control_still_refused(self,_):
        ui=object.__new__(TelegramUI);ui.xdo=Mock();ui.download_arrow=Mock(return_value=False)
        with self.assertRaisesRegex(UIError,'No file was opened'):ui.ready_download_control({'y':430},lambda:False)

    def test_single_album_link_preserves_exact_message_identity(self):
        link='https://t.me/c/123456789/200/300'
        self.assertEqual(attachment_link(link+'?single'),link)
        self.assertEqual(attachment_link(link),link)
        for bad in (link+'?other',link+'?single#other',link.replace('123456789','12345'),link+'/1'):
            with self.assertRaises(UIError):attachment_link(bad)

    def test_ocr_archive_extension_preserves_month_and_volume_identity(self):
        exact='Atlan Forge - 2026-07.7z.002'
        self.assertTrue(attachment_name_matches(exact,'Atlan Forge - 2026-07.72.002'))
        self.assertTrue(attachment_name_matches('Atlan Forge - 2026-08.7z','Atlan Forge - 2026-08.72'))
        for wrong in ('Atlan Forge - 2026-07.72.001','Atlan Forge - 2026-08.72.002',
                      'Atlan Forge - 2025-07.72.002','Atlan Forge - 2026-07.72.0022'):
            self.assertFalse(attachment_name_matches(exact,wrong),wrong)

    def make_ui(self):
        ui=object.__new__(TelegramUI)
        ui.click=Mock();ui.choose=Mock()
        ui.lines=Mock(return_value=[
            {'text':'Atlan Forge - 2026-07.7z.001','x':712,'y':473},
            {'text':'Atlan Forge - 2026-07.72.002','x':713,'y':527}])
        ui.copy=Mock(side_effect=['Atlan Forge - 2026-07.7z.002',
                                  'https://t.me/c/123456789/200/301'])
        return ui

    def row(self):
        return {'text':'Atlan Forge - 2026-07.7z.002','x':650,'y':350,'size_text':'132.3 MB'}

    @patch('telegram_ui.time.sleep')
    def test_resolves_only_the_correct_part_and_uses_copied_identity(self,_):
        ui=self.make_ui()
        item=ui.resolve_row(self.row(),'https://t.me/c/123456789/200')
        self.assertEqual(item['source_message_id'],301)
        self.assertEqual(item['filename'],'Atlan Forge - 2026-07.7z.002')
        self.assertEqual(item['position']['y'],527)
        ui.copy.assert_any_call(713,527,'Copy Filename')

    @patch('telegram_ui.time.sleep')
    def test_ambiguous_rows_are_rejected_without_copying(self,_):
        ui=self.make_ui();ui.lines.return_value.append(dict(ui.lines.return_value[1]))
        with self.assertRaisesRegex(UIError,'2 candidates'):ui.resolve_row(self.row(),'https://t.me/c/123456789/200')
        ui.copy.assert_not_called()

    @patch('telegram_ui.time.sleep')
    def test_changed_filename_or_wrong_topic_still_rejected(self,_):
        ui=self.make_ui();ui.copy.side_effect=['Atlan Forge - 2026-07.7z.001']
        with self.assertRaisesRegex(UIError,'Copied filename'):ui.resolve_row(self.row(),'https://t.me/c/123456789/200')
        ui=self.make_ui();ui.copy.side_effect=['Atlan Forge - 2026-07.7z.002','https://t.me/c/123456789/201/301']
        with self.assertRaisesRegex(UIError,'outside the selected topic'):ui.resolve_row(self.row(),'https://t.me/c/123456789/200')


if __name__=='__main__':unittest.main()
