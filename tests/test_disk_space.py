import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

from app.disk_space import GiB, check_capacity, local_status, is_space_error


class CapacityTests(unittest.TestCase):
    def check(self,free_local,free_remote,needed,same=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dest=root/'destination';dest.mkdir()
            device=root.stat().st_dev
            with patch('app.file_delivery.mount_identity',return_value=('mount','source','fs',device if same else device+1)),patch('app.disk_space.shutil.disk_usage',side_effect=[SimpleNamespace(free=free_local),SimpleNamespace(free=free_remote)]):
                return check_capacity(root,dest,needed,needed,filename='Example.7z')

    def test_local_and_destination_failures_identify_the_drive(self):
        error=self.check(GiB,100*GiB,3*GiB)
        self.assertIn('local staging drive',error)
        self.assertIn('5.0 GiB required',error)
        self.assertTrue(is_space_error(error))
        error=self.check(100*GiB,GiB,3*GiB)
        self.assertIn('final destination drive',error)
        self.assertIn('Resume',error)

    def test_shared_drive_needs_space_for_staging_and_verified_copy(self):
        self.assertIsNone(self.check(7*GiB,7*GiB,3*GiB))
        self.assertIn('shared staging/destination drive',self.check(7*GiB,7*GiB,3*GiB,same=True))

    def test_warning_threshold_and_legacy_errors(self):
        with patch('app.disk_space.shutil.disk_usage',return_value=SimpleNamespace(total=100*GiB,free=9*GiB)):
            self.assertTrue(local_status('/')['low'])
        self.assertTrue(is_space_error('Not enough local staging space for this file plus the download reserve.'))

    def test_missing_download_part_does_not_attempt_extraction(self):
        from app.download_worker import Worker
        from app.release_images import ExtractionError
        worker=Worker.__new__(Worker);worker.numbered_rars=set();worker.history=Mock()
        worker.history.was_downloaded.return_value=False;worker.pause=Mock()
        sub={'topic_url':'fixture'}
        record={'sub':sub,'month':'2026-01','source_message_id':1,'filename':'Example.7z.001'}
        worker.planned=[(sub,{'source_message_id':2,'filename':'Example.7z.002'},'2026-01')]
        with patch('app.download_worker.extract_images') as extract:
            with self.assertRaisesRegex(ExtractionError,'download did not finish: Example.7z.002'):
                worker.finish_release([record])
            extract.assert_not_called()
        worker.pause.assert_called_once()

