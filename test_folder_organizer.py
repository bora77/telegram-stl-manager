from test_support import configure_source
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from folder_organizer import inventory, match_files, Organizer, scan_plan
from release_rules import release_month
from subscription_store import SubscriptionStore


class OrganizerTests(unittest.TestCase):
    def test_dry_run_inventory_preserves_files_and_flags_conflicts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'Artist 2024 - May.zip').write_bytes(b'archive')
            (root/'Artist 2024-06.zip').write_bytes(b'archive')
            (root/'Loyalty February-March-April 2025.zip').write_bytes(b'archive')
            (root/'2024-06').mkdir();(root/'2024-06/Artist 2024-06.zip').write_bytes(b'existing')
            before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            plan=inventory(root)
            self.assertEqual({r['action'] for r in plan},{'move','conflict','review','keep'})
            self.assertEqual(before,{str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()})
            self.assertIsNone(release_month('Loyalty February-March-April 2025.zip'))
            self.assertEqual(release_month('AtlanForge - Octobre 2024 - Loyalty.zip'),'2024-10')

    def test_name_and_size_mapping_tolerates_spacing_and_ocr_but_preserves_numbers(self):
        files=[{'filename':'Atlan Forge - 2026-01.7z','size':1048576,'action':'move'},
               {'filename':'Atlan Forge - 2026-02.7z','size':1048576,'action':'move'}]
        rows=[{'filename':'Adan Forge - 2026-01.72','size_text':'1.0 MB'}]
        match_files(files,rows,set())
        self.assertTrue(files[0]['would_record']);self.assertEqual(files[1]['telegram_status'],'unmatched')
        self.assertNotIn('source_message_id',files[0])
        match_files(files,rows,{(files[0]['filename'],1048576)})
        self.assertTrue(files[0]['already_recorded']);self.assertFalse(files[0]['would_record'])
        match_files(files,[dict(rows[0],size_text='3.0 MB')],set())
        self.assertEqual(files[0]['telegram_status'],'size_mismatch')
        self.assertEqual(files[0]['telegram_size'],'3.0 MB')
        self.assertEqual(files[0]['telegram_name'],rows[0]['filename'])
        match_files(files,[dict(rows[0],size_text='°32.3 MB')],set())
        self.assertEqual(files[0]['telegram_status'],'size_unreadable')
        self.assertFalse(files[0]['would_record'])

    def test_clipped_names_with_same_size_are_ambiguous(self):
        files=[{'filename':'Atlan Forge - Loyalty Release Alpha.zip','size':1024,'action':'move'},
               {'filename':'Atlan Forge - Loyalty Release Beta.zip','size':1024,'action':'move'}]
        match_files(files,[{'filename':'Atlan Forge - Loyalty Release...','size_text':'1.0 KB'}],set())
        self.assertTrue(all(f['telegram_status']=='ambiguous' for f in files))
        self.assertFalse(any(f['would_record'] for f in files))

    def test_clipped_archive_and_photo_are_distinguished_by_size(self):
        files=[{'filename':'Atlan Forge - 2022-10 - Reward - Cybernetic Pteranodon.rar','size':383082215,'action':'move'}]
        rows=[{'filename':'Atlan Forge - 2022-10 - Reward - Cyberneti','size_text':'365.3 MB'},
              {'filename':'Atlan Forge - 2022-10 - Reward - Cyberneti...','size_text':'140.5 KB'}]
        match_files(files,rows,set())
        self.assertEqual(files[0]['telegram_status'],'matched')
        self.assertEqual(files[0]['telegram_size'],'365.3 MB')

    def test_scan_uses_one_exact_cli_listing_without_changing_history(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SubscriptionStore(configure_source(temp));organizer=Organizer(store)
            store.atomic_write(organizer.path,{'id':'test','state':'starting','heartbeat':__import__('time').time(),
                'creator':'Example','topic_url':'https://t.me/c/123456789/200','attachments':[],'warnings':[],
                'files':[{'filename':'Example 2026-01.zip','size':1024,'action':'move'}]})
            class UI:
                def __init__(self):self.opens=0
                def list_files(self,topic,stopped,status,on_files=None):
                    self.opens+=1;status('Reading exact metadata')
                    files=[{'filename':'Example 2026-01.zip','bytes_total':1024,'source_message_id':1,'message_url':topic+'/1'},
                           {'filename':'Example 2025-12.zip','bytes_total':1024,'source_message_id':2,'message_url':topic+'/2'}]
                    on_files(files[:1])
                    self.partial=organizer.status()
                    return files
                def close(self):pass
            ui=UI()
            with patch('folder_organizer.TelegramCLI',return_value=ui):scan_plan(store,'test')
            plan=organizer.status()
            self.assertEqual(ui.opens,1);self.assertEqual(plan['state'],'completed')
            self.assertEqual(ui.partial['state'],'scanning');self.assertEqual(ui.partial['rows_read'],1)
            self.assertEqual(ui.partial['files'][0]['telegram_status'],'matched')
            self.assertTrue(plan['files'][0]['would_record'])
            self.assertEqual(plan['files'][0]['source_message_id'],1)
            with store.history.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM downloads').fetchone()[0],0)

    def test_cli_mapping_uses_exact_bytes_and_never_fuzzy_ocr_names(self):
        files=[{'filename':'Example 2026-01.7z','size':1025,'action':'move'}]
        item={'filename':'Example 2026-01.7z','bytes_total':1024,'source_message_id':1,'message_url':'https://t.me/c/123456789/200/1'}
        match_files(files,[item],set());self.assertEqual(files[0]['telegram_status'],'size_mismatch')
        item['bytes_total']=1025
        match_files(files,[item],set());self.assertTrue(files[0]['would_record'])
        item['filename']='Example 2026-01.72'
        match_files(files,[item],set());self.assertEqual(files[0]['telegram_status'],'unmatched')

    def test_apply_is_unavailable_and_preview_blocks_download_start(self):
        with tempfile.TemporaryDirectory() as temp,patch('run_manager.subprocess.Popen') as launch:
            store=SubscriptionStore(configure_source(temp));organizer=Organizer(store)
            with self.assertRaises(ValueError):organizer.start({'dry_run':False})
            store.atomic_write(organizer.path,{'state':'scanning','heartbeat':__import__('time').time()})
            with self.assertRaises(FileExistsError):store.runs.start({'revision':0,'config_revision':0})
            launch.assert_not_called()


if __name__=='__main__':unittest.main()
