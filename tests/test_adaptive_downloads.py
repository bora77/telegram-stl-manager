from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import hashlib
import os
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from app import adaptive_downloads as ad
from app import download_worker
from app.file_delivery import release_lock
from app.subscription_store import SubscriptionStore
from tests.test_support import configure_source


def planned(mid, dc=1, month='2026-01', creator='Example'):
    sub = {'creator': creator, 'creator_folder': creator, 'topic_url': 'https://t.me/c/123456789/200',
           'download_scope': 'all_and_future'}
    item = {'source_message_id': mid, 'document_id': mid + 1000, 'dc_id': dc,
            'filename': f'Example {month} {mid}.zip', 'bytes_total': 100_000_000,
            'topic_url': sub['topic_url'], 'message_url': sub['topic_url'] + '/' + str(mid)}
    return sub, item, month


def job(mid, dc=1, month=None):
    sub, item, month = planned(mid, dc, month or f'2026-{mid:02}')
    return ad.Job(mid, sub, item, month, mid)


def active(j, mbps, role='baseline', now=100):
    j.state, j.phase, j.role = 'running', 'downloading', role
    j.network_started = 0
    j.samples = deque([(now - 30, 0)])
    j.done = int(mbps * 1e6 * 30)
    return j


class PolicyTests(unittest.TestCase):
    def test_look_ahead_uses_another_dc_while_old_slow_file_continues(self):
        old, same, other = active(job(1), 9), job(2), job(3, 4)
        self.assertIs(ad.choose_job([old, same, other], 100, 28e6, 60, {}), other)
        self.assertEqual(old.state, 'running')
        self.assertEqual(other.role, 'look_ahead')

    def test_warmup_target_and_three_file_limit_prevent_unneeded_downloads(self):
        old, later = active(job(1), 9), job(2, 4)
        self.assertIsNone(ad.choose_job([old, later], 100, 28e6, 80, {}))
        old = active(old, 27)
        self.assertIsNone(ad.choose_job([old, later], 100, 28e6, 60, {}))
        jobs = [active(job(i), 2) for i in range(1, 4)] + [job(4)]
        self.assertIsNone(ad.choose_job(jobs, 100, 28e6, 60, {}))

    def test_oldest_lane_returns_and_processing_month_stays_exclusive(self):
        fast, oldest, newer = active(job(3, 4), 12, 'look_ahead'), job(1), job(2)
        self.assertIs(ad.choose_job([fast, oldest, newer], 100, 28e6, 60, {}), oldest)
        oldest.state = 'processing'
        same_month = job(4, month=oldest.month)
        self.assertIs(ad.choose_job([fast, oldest, same_month, newer], 100, 28e6, 60, {}), newer)

    def test_known_faster_dc_and_archive_companions_win_extra_slots(self):
        jobs = [active(job(1), 9), job(2, 2), job(3, 4)]
        self.assertIs(ad.choose_job(jobs, 100, 28e6, 60, {2: 5e6, 4: 20e6}), jobs[2])
        previous, companion = job(4, 4), job(5, 4, '2026-04')
        previous.item['filename'] = 'Example 2026-04.7z.001'
        companion.item['filename'] = 'Example 2026-04.7z.002'
        previous.state = 'handled'
        self.assertIs(ad.choose_job(jobs + [previous, companion], 100, 28e6, 60, {}), companion)


class CoordinatorTests(unittest.TestCase):
    def fixture(self, root, entries):
        store = SubscriptionStore(configure_source(root))
        state = root / 'telegram'; state.mkdir()
        base = root / 'destination'; base.mkdir()
        store.atomic_write(store.config_path, {'revision': 1, 'download_directory': str(base),
                                              'download_bandwidth_target_mbps': 28, 'adaptive_downloads': True})
        run = {'id': 'adaptive-test', 'download_directory': str(base), 'config': store.config(),
               'subscriptions': [entries[0][0]], 'state': 'starting', 'warnings': []}
        return store, state, run

    def test_resume_preserves_prefetched_transfer_timing_and_rejects_changed_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);entry=planned(1);sub,item,month=entry
            store,state,run=self.fixture(root,[entry]);store.atomic_write(store.runs.path,run)
            item['bytes_total']=4
            transfer=state/'transfers/1';transfer.mkdir(parents=True)
            (transfer/'payload.part').write_bytes(b'data')
            store.atomic_write(transfer/'item.json',item)
            store.atomic_write(transfer/'complete.json',{'bytes':4,'total':4,'message_id':1,
                'filename':item['filename'],'sha256':hashlib.sha256(b'data').hexdigest()})
            row=store.history.register(source_message_id=1,creator=sub['creator'],topic_url=sub['topic_url'],
                filename=item['filename'],bytes_total=4,release_month=month,batch_id=run['id'])
            metrics={'download_started_at':'2026-09-14T10:00:00+00:00','download_finished_at':'2026-09-14T10:00:01+00:00',
                     'download_seconds':1,'download_speed_bps':None,'download_average_bps':4}
            store.history.record_transfer(row['id'],4,4,metrics)
            with patch.object(download_worker,'ROOT',root),patch.object(download_worker,'STATE',state):
                worker=download_worker.Worker(run['id']);worker.telegram=type('CLI',(),{'state':state})()
                worker.queue_item(sub,item,month)
                with store.history.connect() as db:saved=dict(db.execute('SELECT * FROM downloads WHERE id=?',(row['id'],)).fetchone())
                for key,value in metrics.items():self.assertEqual(saved[key],value)
                (transfer/'payload.part').write_bytes(b'changed size')
                worker.queue_item(sub,item,month)
                with store.history.connect() as db:saved=dict(db.execute('SELECT * FROM downloads WHERE id=?',(row['id'],)).fetchone())
                self.assertIsNone(saved['download_finished_at'])
                self.assertEqual(saved['bytes_downloaded'],0)

    def test_ready_file_does_not_wait_for_slow_file_and_stop_cancels_only_owned_jobs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ad, 'WINDOW', .05):
            root = Path(tmp); entries = [planned(1), planned(2, 4, '2026-02')]
            store, state, run = self.fixture(root, entries)
            slow_started = threading.Event(); slow_stopped = threading.Event(); calls = []
            class CLI:
                def __init__(self, **kwargs): pass
                def download(self, item, progress, stopped, status):
                    calls.append(item['source_message_id'])
                    status({'event': 'download_start'})
                    if item['source_message_id'] == 1:
                        slow_started.set()
                        while not stopped(): time.sleep(.005)
                        slow_stopped.set(); raise RuntimeError('Stopped fixture')
                    path = state / str(item['source_message_id']); path.write_bytes(b'kept')
                    progress(item['bytes_total'], item['bytes_total'])
                    return path
                def close(self): pass
            with ad.AdaptiveDownloads(store, run, entries, lambda: False, lambda j: False,
                                      cli_factory=CLI, state=state, interval=.005) as scheduler:
                self.assertTrue(slow_started.wait(2))
                with ThreadPoolExecutor(1) as pool:
                    ready = pool.submit(scheduler.next_ready).result(timeout=3)
                self.assertEqual(ready.item['source_message_id'], 2)
                self.assertFalse(slow_stopped.is_set())
                self.assertEqual(ready.source.read_bytes(), b'kept')
                scheduler.handled(ready)
            self.assertTrue(slow_stopped.is_set())
            self.assertEqual(calls, [1, 2])
            self.assertTrue((state / '2').exists())
            self.assertFalse(json.loads(scheduler.status_path.read_text())['active'])

    def test_organizer_busy_month_yields_to_other_month_then_rechecks_history(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ad, 'WINDOW', .02):
            root = Path(tmp); entries = [planned(1), planned(2, 4, '2026-02')]
            store, state, run = self.fixture(root, entries); calls = []
            class CLI:
                def __init__(self, **kwargs): pass
                def download(self, item, *args):
                    calls.append(item['source_message_id']); path = state / str(item['source_message_id'])
                    path.write_bytes(b'fixture'); return path
                def close(self): pass
            # Establish the external lease before starting the coordinator;
            # holding its condition while waiting on its own transfer deadlocks.
            lease = release_lock(root, run['download_directory'], 'Example', '2026-01')
            lease.__enter__()
            with ad.AdaptiveDownloads(store, run, entries, lambda: False, lambda j: False,
                                      cli_factory=CLI, state=state, interval=.005) as scheduler:
                try:
                    with ThreadPoolExecutor(1) as pool:
                        ready = pool.submit(scheduler.next_ready).result(timeout=3)
                    self.assertEqual(ready.item['source_message_id'], 2)
                    scheduler.handled(ready)
                    store.history.complete(scheduler.jobs[0].row_id, destination=root/'already.zip',
                                           verified_size=entries[0][1]['bytes_total'], sha256='a'*64)
                finally: lease.__exit__(None, None, None)
                with ThreadPoolExecutor(1) as pool:
                    ready = pool.submit(scheduler.next_ready).result(timeout=4)
                scheduler.handled(ready)
                scheduler.thread.join(timeout=3)
                self.assertFalse(scheduler.thread.is_alive())
                self.assertIsNone(scheduler.next_ready())
            self.assertEqual(calls, [2])

    def test_disk_reservations_include_all_inflight_files(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ad, 'WINDOW', .03), patch.object(ad, 'DISK_RESERVE', 0):
            root = Path(tmp); entries = [planned(1), planned(2, 4, '2026-02')]
            store, state, run = self.fixture(root, entries); started = threading.Event(); calls = []
            class CLI:
                def __init__(self, **kwargs): pass
                def download(self, item, progress, stopped, status):
                    calls.append(item['source_message_id']); started.set(); status({'event': 'download_start'})
                    while not stopped(): time.sleep(.005)
                    raise RuntimeError('Stopped fixture')
                def close(self): pass
            usage = type('Usage', (), {'free': 150_000_000})()
            with patch.object(ad.shutil, 'disk_usage', return_value=usage), ad.AdaptiveDownloads(
                    store, run, entries, lambda: False, lambda j: False, cli_factory=CLI, state=state, interval=.005):
                self.assertTrue(started.wait(2)); time.sleep(.12)
                self.assertEqual(calls, [1])

    def test_worker_moves_fast_release_before_slow_one_and_keeps_failed_item_for_review(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ad, 'WINDOW', .05):
            root = Path(tmp)
            entries = [planned(1), planned(2, 4, '2026-02'), planned(3, 4, '2026-03')]
            store, state, run = self.fixture(root, entries)
            payload = root / 'fixture.zip'
            with zipfile.ZipFile(payload, 'w') as archive: archive.writestr('model.stl', b'model')
            data = payload.read_bytes()
            for _, item, _ in entries: item['bytes_total'] = len(data)
            store.atomic_write(store.runs.path, run)
            slow_started = threading.Event(); release_slow = threading.Event()
            class CLI:
                use_service = True
                def __init__(self, **kwargs): self.state = state
                def list_files(self, *args, **kwargs): return [item for _, item, _ in entries]
                def download(self, item, progress, stopped, status):
                    mid = item['source_message_id']; status({'event': 'download_start'})
                    if mid == 1:
                        slow_started.set()
                        while not release_slow.wait(.005):
                            if stopped(): raise RuntimeError('Stopped fixture')
                    if mid == 3: raise RuntimeError('Fixture transfer failed')
                    path = state / str(mid); path.write_bytes(data)
                    progress(len(data), len(data)); status({'event': 'download_finished'})
                    return path
                def close(self): pass
            with patch.object(download_worker, 'ROOT', root), patch.object(download_worker, 'STATE', state), \
                 patch.object(download_worker, 'TelegramCLI', CLI), ThreadPoolExecutor(1) as pool:
                worker = download_worker.Worker(run['id'])
                future = pool.submit(worker.execute)
                try:
                    self.assertTrue(slow_started.wait(3))
                    deadline = time.monotonic() + 5
                    while not store.history.was_downloaded(2) and time.monotonic() < deadline: time.sleep(.02)
                    self.assertTrue(store.history.was_downloaded(2), worker.run)
                    self.assertFalse(store.history.was_downloaded(1))
                    self.assertEqual((Path(run['download_directory'])/'Example'/'2026-02'/entries[1][1]['filename']).read_bytes(), data)
                finally: release_slow.set()
                future.result(timeout=5)
            self.assertTrue(store.history.was_downloaded(1))
            self.assertFalse(store.history.was_downloaded(3))
            self.assertEqual(worker.run['state'], 'needs_review')
            self.assertIn('Fixture transfer failed', ' '.join(worker.run['warnings']))

    def test_adaptive_split_archive_waits_for_every_volume_before_delivery(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ad, 'WINDOW', .02):
            root = Path(tmp)
            image = root / 'preview.jpg'; image.write_bytes(os.urandom(9000))
            subprocess.run(['7z', 'a', '-mx0', '-v4k', str(root/'Example 2026-01.7z'), str(image)],
                           stdout=subprocess.DEVNULL, check=True)
            entries = []
            for mid, path in enumerate(sorted(root.glob('*.7z.*'), reverse=True), 1):
                sub, item, month = planned(mid)
                item.update(filename=path.name, bytes_total=path.stat().st_size)
                entries.append((sub, item, month))
            store, state, run = self.fixture(root, entries)
            store.atomic_write(store.runs.path, run)
            calls = []
            class CLI:
                use_service = True
                def __init__(self, **kwargs): self.state = state
                def list_files(self, *args, **kwargs): return [item for _, item, _ in entries]
                def download(self, item, progress, stopped, status):
                    self_test.assertFalse(any(store.history.was_downloaded(i) for i in range(1, 4)))
                    calls.append(item['filename'])
                    path = state / item['filename']; path.write_bytes((root/item['filename']).read_bytes())
                    status({'event': 'download_start'}); progress(item['bytes_total'], item['bytes_total'])
                    status({'event': 'download_finished'}); return path
                def close(self): pass
            self_test = self
            with patch.object(download_worker, 'ROOT', root), patch.object(download_worker, 'STATE', state), \
                 patch.object(download_worker, 'TelegramCLI', CLI):
                worker = download_worker.Worker(run['id']); worker.execute()
            self.assertEqual(worker.run['state'], 'completed', worker.run['message'])
            self.assertEqual(calls, sorted(calls))
            self.assertTrue(all(store.history.was_downloaded(i) for i in range(1, 4)))
            images = list((Path(run['download_directory'])/'Example/2026-01/release_images').iterdir())
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0].read_bytes(), image.read_bytes())


if __name__ == '__main__': unittest.main()
