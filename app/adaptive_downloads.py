"""Finite, capacity-aware downloads from an already validated manual queue.

Only this coordinator starts transfers. Each owns its CLI request and database
metrics; the main worker alone extracts images, delivers files and edits run.json.
"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import threading
import time

from app.file_delivery import release_lock
from app.release_images import volume_key
from app.telegram_cli import CLIError, TelegramCLI, CLI_STATE, completed_transfer_receipt
from app.transfer_metrics import TransferMeter

WINDOW = 30
MAX_TRANSFERS = 3
READY_LIMIT = 8 * 1024**3
DISK_RESERVE = 2 * 1024**3


@dataclass
class Job:
    order: int
    sub: dict
    item: dict
    month: str
    row_id: int
    state: str = 'pending'
    phase: str = 'queued'
    role: str = 'baseline'
    source: object = None
    error: str = ''
    done: int = 0
    network_started: object = None
    started: float = 0
    retry_at: float = 0
    samples: deque = field(default_factory=deque)
    endpoint: object = None
    detail: str = ''

    @property
    def size(self): return self.item['bytes_total']

    @property
    def month_key(self): return self.sub['creator_folder'].casefold(), self.month

    @property
    def archive_key(self): return self.month_key, volume_key(self.item['filename'])[0]

    def rate(self, now):
        if self.network_started is None or self.phase != 'downloading': return 0.0
        while len(self.samples) > 1 and self.samples[1][0] <= now - WINDOW:
            self.samples.popleft()
        if not self.samples: return 0.0
        at, done = self.samples[0]
        elapsed = now - at
        return max(0, self.done - done) / elapsed if elapsed > 0 else 0.0


def choose_job(jobs, now, target_bps, last_start, dc_rates, limit=MAX_TRANSFERS):
    """Grow gradually; preserve an oldest-first lane alongside faster look-ahead."""
    active = [j for j in jobs if j.state == 'running']
    blocked = {j.month_key for j in jobs if j.state in ('running', 'ready', 'processing')}
    pending = [j for j in jobs if j.state == 'pending' and j.retry_at <= now and j.month_key not in blocked]
    if not pending or len(active) >= limit: return None
    if sum(j.size for j in jobs if j.state in ('ready', 'processing')) >= READY_LIMIT: return None
    if active:
        # Ignore startup/probe noise and never launch more files at target speed.
        if now - last_start < WINDOW: return None
        if sum(j.rate(now) for j in active) >= target_bps * .95: return None
    if not active or not any(j.role == 'baseline' for j in active):
        job = min(pending, key=lambda j: j.order)
        job.role = 'baseline'
        return job
    busy_dcs = {j.item['dc_id'] for j in active}
    staged_groups = {j.archive_key for j in jobs if j.state == 'handled'}
    job = min(pending, key=lambda j: (
        j.item['dc_id'] in busy_dcs,
        j.archive_key not in staged_groups,
        -dc_rates.get(j.item['dc_id'], 0), j.order))
    job.role = 'look_ahead'
    return job


class AdaptiveDownloads:
    def __init__(self, store, run, planned, stopped, reuse_staged, *, cli_factory=TelegramCLI,
                 state=CLI_STATE, clock=time.monotonic, interval=.2):
        self.store, self.run = store, run
        self.root, self.history = store.root, store.history
        self.external_stopped, self.reuse_staged = stopped, reuse_staged
        self.cli_factory, self.state, self.clock, self.interval = cli_factory, Path(state), clock, interval
        self.config = dict(run.get('config', store.config()))
        self.target = self.config.get('download_bandwidth_target_mbps', 28)
        self.enabled = self.config.get('adaptive_downloads', True)
        self.condition = threading.Condition(threading.RLock())
        self.cancel = threading.Event()
        self.thread = None
        self.fatal = None
        self.jobs = []
        self.dc_rates = {}
        self.release_servers = {}
        self.last_start = float('-inf')
        self.message = 'Starting the saved download queue.'
        self.status_path = self.root / 'data/download-scheduler.json'
        seen = set()
        for order, (sub, item, month) in enumerate(planned):
            mid = item['source_message_id']
            if mid in seen: raise ValueError('Duplicate attachment in the saved queue.')
            seen.add(mid)
            row = self.history.register(source_message_id=mid, creator=sub['creator'], topic_url=sub['topic_url'],
                                        filename=item['filename'], bytes_total=item['bytes_total'],
                                        release_month=month, batch_id=run['id'])
            self.jobs.append(Job(order, sub, item, month, row['id']))

    def stopped(self): return self.cancel.is_set() or self.external_stopped()

    def __enter__(self):
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.thread = threading.Thread(target=self._coordinate, name='adaptive-downloads')
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.cancel.set()
        if self.thread: self.thread.join()

    def next_ready(self):
        with self.condition:
            while True:
                if self.fatal: raise CLIError(str(self.fatal))
                if all(j.state == 'handled' for j in self.jobs): return None
                if self.stopped(): raise CLIError('Stopped by request; completed staged files are retained.')
                ready = [j for j in self.jobs if j.state == 'ready']
                if ready:
                    job = min(ready, key=lambda j: j.order)
                    job.state = 'processing'
                    return job
                self.condition.wait(.2)

    def handled(self, job):
        with self.condition:
            job.state = 'handled'
            self.condition.notify_all()

    def _snapshot(self, now, active=True):
        running = [j for j in self.jobs if j.state == 'running']
        transfers = [{'id': j.row_id, 'filename': j.item['filename'], 'creator': j.sub['creator'],
                      'dc_id': j.item['dc_id'], 'bytes': j.done, 'total': j.size,
                      'speed_bps': j.rate(now), 'phase': j.phase, 'detail': j.detail,
                      'endpoint': j.endpoint, 'look_ahead': j.role == 'look_ahead'} for j in running]
        return {'batch_id': self.run['id'], 'active': active, 'updated_at': time.time(),
                'target_mbps': self.target, 'enabled': self.enabled, 'max_transfers': MAX_TRANSFERS if self.enabled else 1,
                'message': self.message, 'transfers': transfers,
                'ready_files': sum(j.state in ('ready', 'processing') and not j.error for j in self.jobs)}

    def _coordinate(self):
        pool = ThreadPoolExecutor(max_workers=MAX_TRANSFERS, thread_name_prefix='telegram-file')
        try:
            saved = settings_read = float('-inf')
            while not self.stopped():
                now = self.clock()
                if now - settings_read >= 5:
                    config = self.store.config()
                    with self.condition:
                        self.target = config.get('download_bandwidth_target_mbps', self.target)
                        self.enabled = config.get('adaptive_downloads', True)
                    settings_read = now
                with self.condition:
                    if all(j.state == 'handled' for j in self.jobs): break
                    job = choose_job(self.jobs, now, self.target * 1e6, self.last_start, self.dc_rates,
                                     MAX_TRANSFERS if self.enabled else 1)
                    if job:
                        reserved = sum(max(0, j.size - j.done) for j in self.jobs if j.state == 'running')
                        free = shutil.disk_usage(self.state).free
                        if free >= reserved + job.size + DISK_RESERVE or self.reuse_staged(job) or completed_transfer_receipt(job.item,self.state):
                            job.state, job.phase, job.started = 'running', 'starting', now
                            self.last_start = now
                            self.message = ('Using spare bandwidth for ' if job.role == 'look_ahead' else 'Downloading ') + job.item['filename']
                            pool.submit(self._download, job).add_done_callback(self._job_finished)
                        elif any(j.state in ('running', 'ready', 'processing') for j in self.jobs):
                            self.message = 'Waiting for local files to be processed before reserving more disk space.'
                        else:
                            self._failure(job, 'Not enough local staging space for this file plus the download reserve.')
                    if now - saved >= 1:
                        snapshot = self._snapshot(now)
                        self.store.atomic_write(self.status_path, snapshot)
                        saved = now
                self.cancel.wait(self.interval)
        except BaseException as error:
            with self.condition:
                self.fatal = error
                self.cancel.set()
                self.condition.notify_all()
        finally:
            self.cancel.set()
            pool.shutdown(wait=True, cancel_futures=True)
            with self.condition:
                self.store.atomic_write(self.status_path, self._snapshot(self.clock(), active=False))
                self.condition.notify_all()

    def _failure(self, job, message):
        job.error, job.phase, job.state = str(message), 'failed', 'ready'
        with self.history.connect() as db:
            db.execute("UPDATE downloads SET state='paused',error=?,download_speed_bps=NULL WHERE id=? AND state!='downloaded'",
                       (job.error, job.row_id))
        self.condition.notify_all()

    def _job_finished(self, future):
        # Even an error while recording a failure or closing the adapter must
        # wake the consumer rather than leave a permanently "running" row.
        if future.cancelled(): return
        error = future.exception()
        if error is not None:
            with self.condition:
                self.fatal = error
                self.cancel.set()
                self.condition.notify_all()

    def _download(self, job):
        cli = None
        try:
            # One artist/month stays serialized with organization and delivery.
            # A busy month yields its slot so other months can continue.
            with release_lock(self.root, self.run['download_directory'], job.sub['creator_folder'], job.month,
                              self.stopped, blocking=False):
                if self.history.was_downloaded(job.item['source_message_id']) or self.reuse_staged(job):
                    with self.condition:
                        job.state, job.phase = 'ready', 'staged'
                        self.condition.notify_all()
                    return
                cli = self.cli_factory(root=self.root, state=self.state, config=self.config)
                cli.release_servers = self.release_servers
                meter = None
                persisted = float('-inf')

                def progress(done, total, estimate=None):
                    nonlocal meter, persisted
                    now = self.clock()
                    with self.condition:
                        job.done = done
                        if job.network_started is not None:
                            if meter is None: meter = TransferMeter(clock=self.clock)
                            if not job.samples or now - job.samples[-1][0] >= 1 or done == total:
                                job.samples.append((now, done))
                            metrics = meter.sample(done, finished=done == total)
                        else:
                            # Reused receipts are not fresh, impossibly fast transfers.
                            metrics = None
                    if metrics and (now - persisted >= 1 or done == total):
                        self.history.record_transfer(job.row_id, done, total, metrics, estimate)
                        persisted = now

                def status(event):
                    nonlocal meter
                    now = self.clock()
                    kind = event.get('event')
                    with self.condition:
                        if kind == 'download_start':
                            job.network_started = now
                            job.samples = deque([(now, 0)])
                            job.phase, job.done = 'downloading', 0
                            meter = TransferMeter(clock=self.clock)
                        elif kind == 'download_resumed': job.phase = 'downloading'
                        elif kind == 'download_finished': job.phase = 'verifying'
                        elif kind == 'server_selected':
                            job.endpoint = event.get('endpoint')
                        elif kind in ('server_testing', 'server_slow_retest', 'server_retry'):
                            job.phase = 'testing_servers'
                        job.detail = {
                            'server_testing': 'Comparing download servers',
                            'server_slow_retest': 'Comparing servers after a slowdown',
                            'server_retry': 'Retrying the connection',
                            'download_finished': 'Verifying the completed file',
                            'download_start': '', 'download_resumed': '',
                        }.get(kind, job.detail)

                source = cli.download(job.item, progress, self.stopped, status)
                with self.condition:
                    if meter:
                        elapsed = max(0, self.clock() - job.network_started)
                        if elapsed >= 10: self.dc_rates[job.item['dc_id']] = job.done / elapsed
                    job.source, job.done, job.phase = source, job.size, 'staged'
                with self.history.connect() as db:
                    db.execute("UPDATE downloads SET state='queued',bytes_downloaded=?,download_speed_bps=NULL WHERE id=? AND state!='downloaded'",
                               (job.size, job.row_id))
            with self.condition:
                job.state = 'ready'
                self.condition.notify_all()
        except BlockingIOError:
            with self.condition:
                job.state, job.phase, job.retry_at = 'pending', 'waiting_for_organizer', self.clock() + 2
                self.message = 'Organization is using ' + job.sub['creator'] + ' · ' + job.month + '; checking other releases.'
                self.condition.notify_all()
        except BaseException as error:
            with self.condition: self._failure(job, error)
        finally:
            if cli: cli.close()
