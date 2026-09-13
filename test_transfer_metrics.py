from test_support import TEST_SOURCE
from datetime import datetime,timezone
from pathlib import Path
import tempfile
import unittest
from download_history import DownloadHistory
from transfer_metrics import TransferMeter

class MetricsTests(unittest.TestCase):
    def test_speed_and_duration_exclude_completion_check_and_nas_move(self):
        now=[0.0]
        meter=TransferMeter(clock=lambda:now[0],started_at=datetime(2026,9,12,tzinfo=timezone.utc))
        meter.sample(0)
        now[0]=5;first=meter.sample(500)
        self.assertEqual(first['download_speed_bps'],100)
        now[0]=10;meter.sample(1000)
        now[0]=14;done=meter.sample(1000,finished=True)
        self.assertEqual(done['download_seconds'],10)
        self.assertEqual(done['download_average_bps'],100)
        self.assertIsNone(done['download_speed_bps'])
        self.assertEqual(done['download_finished_at'],'2026-09-12T00:00:10+00:00')
    def test_live_speed_falls_to_zero_when_growth_stops(self):
        now=[0.0];meter=TransferMeter(clock=lambda:now[0])
        now[0]=1;meter.sample(1000)
        for tick in range(2,9):now[0]=tick;last=meter.sample(1000)
        self.assertEqual(last['download_speed_bps'],0)
    def test_history_keeps_metrics_without_inventing_old_timings(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'history.sqlite3';history=DownloadHistory(path, source=TEST_SOURCE)
            item=history.register(source_message_id=1,creator='Example',topic_url='https://t.me/c/123456789/200',filename='example.zip')
            self.assertIsNone(item['download_seconds'])
            now=[0.0];meter=TransferMeter(clock=lambda:now[0])
            now[0]=10;metrics=meter.sample(1000,finished=True)
            history.record_transfer(item['id'],500,None,metrics,estimate=1000)
            self.assertEqual(history.queue()['progress_total'],1000)
            self.assertTrue(history.queue()['total_is_estimate'])
            history.record_transfer(item['id'],1000,1000,metrics)
            self.assertFalse(history.queue()['total_is_estimate'])
            history.complete(item['id'],destination=str(Path(temp)/'moved.zip'),verified_size=1000,sha256='0'*64)
            reopened=DownloadHistory(path, source=TEST_SOURCE)
            done=reopened.queue()['recent_completed'][0]
            self.assertEqual(done['download_seconds'],10)
            self.assertEqual(done['download_average_bps'],100)
            reopened.record_transfer(item['id'],0,None,meter.sample(1000))
            self.assertEqual(reopened.queue()['recent_completed'][0]['download_seconds'],10)

if __name__=='__main__':unittest.main()
