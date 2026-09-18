"""Deterministic Telegram CLI adapter. No GUI, OCR, AI or background scheduler."""
import codecs
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
from app.source_scope import load_source

ROOT = Path(__file__).resolve().parent.parent
# Preserve completed staging from the former Desktop worker across migration.
STATE = Path.home() / '.local/share/telegram-stl-desktop'
CLI_STATE = Path.home() / '.local/share/telegram-stl-tdl'


class CLIError(RuntimeError):
    pass


def require_release_collage(archive, *, action="uploading"):
    """Every archive upload through the app requires its release's valid collage."""
    from app.collages import collage_filename
    from PIL import Image
    archive=Path(archive)
    collage=archive.parent/collage_filename({'folder':archive.parent.parent.name,'month':archive.parent.name})
    if not collage.is_file() or collage.is_symlink():
        raise CLIError('Create and save a collage for this release before '+action+': '+collage.name)
    try:
        if not 0<collage.stat().st_size<=32*1024*1024:raise ValueError('Invalid collage size.')
        with Image.open(collage) as im:
            if im.format!='JPEG' or im.width*im.height>40_000_000:raise ValueError('Invalid collage image.')
            im.verify()
    except (OSError,ValueError,Image.DecompressionBombError) as error:
        raise CLIError('The release collage is damaged or invalid. Save it again before '+action+'.') from error
    return collage


def safe_filename(name):
    return (isinstance(name, str) and name not in ('', '.', '..')
            and not re.search(r'[/\\\x00-\x1f]', name) and len(name.encode()) <= 255)


def parse_export(data, topic_url, *, scope=None):
    scope = scope or load_source()
    topic = scope.topic_id(topic_url)
    if topic is None or data.get('id') != scope.chat_id or not isinstance(data.get('messages'), list):
        raise CLIError('Telegram export is outside the approved group or malformed.')
    files = []; seen = set()
    for message in data['messages']:
        raw = message.get('raw')
        if not isinstance(raw, dict) or raw.get('PeerID') != {'ChannelID': scope.chat_id}:
            raise CLIError('Telegram returned an unverified message scope.')
        mid = message.get('id')
        if not isinstance(mid, int) or mid <= 0 or raw.get('ID') != mid or mid in seen:
            raise CLIError('Telegram returned an invalid or duplicate message identity.')
        seen.add(mid)
        reply = raw.get('ReplyTo') or {}
        parent = reply.get('ReplyToTopID') or reply.get('ReplyToMsgID')
        if mid != topic and (not reply.get('ForumTopic') or parent != topic):
            raise CLIError('Telegram returned a message outside the selected creator topic.')
        document = (raw.get('Media') or {}).get('Document')
        if not isinstance(document, dict) or 'Size' not in document:
            continue  # Compressed photos are separate from document attachments.
        name = message.get('file')
        names = [a['FileName'] for a in document.get('Attributes', []) if 'FileName' in a]
        if names and names != [name]:
            raise CLIError('Telegram attachment filename metadata is inconsistent.')
        size, dc, document_id = (document.get(k) for k in ('Size', 'DCID', 'ID'))
        if not all(type(x) is int for x in (size, dc, document_id)) or size < 0 or dc <= 0 or document_id == 0:
            raise CLIError('Telegram attachment has invalid size or identity metadata.')
        item={'filename': name, 'source_message_id': mid,
                      'message_url': topic_url + '/' + str(mid), 'topic_url': topic_url,
                      'bytes_total': size, 'dc_id': dc, 'document_id': document_id,
                      'size_text': f'{size:,} bytes', 'unsafe_filename': not safe_filename(name)}
        mime=document.get('MimeType')
        # Retain image hints for files without a recognizable extension. Avoid
        # changing existing archive identities used by staged transfer receipts.
        if isinstance(mime,str) and mime.strip().casefold().startswith('image/'):item['mime_type']=mime
        files.append(item)
    return sorted(files, key=lambda item: item['source_message_id'])


class ExportProgress:
    """Read complete messages from the CLI's growing JSON export for previews.

    Every displayed message passes the same scope and metadata checks as the
    final export. This stream never replaces the complete-scan receipt check.
    """
    def __init__(self, topic, scope):
        self.topic, self.scope = topic, scope
        self.offset = 0
        self.buffer = ''
        self.header = False
        self.seen = set()
        self.decoder = codecs.getincrementaldecoder('utf-8')()

    def read(self, path):
        if not path.exists():return []
        if path.stat().st_size > 128 * 1024**2:
            raise CLIError('Telegram metadata export is too large; scan not accepted.')
        with path.open('rb') as stream:
            stream.seek(self.offset)
            chunk = stream.read(128 * 1024**2 + 1 - self.offset)
            self.offset += len(chunk)
        if self.offset > 128 * 1024**2:
            raise CLIError('Telegram metadata export is too large; scan not accepted.')
        self.buffer += self.decoder.decode(chunk)
        if not self.header:
            # chat.Export writes this fixed envelope before its message array.
            header = re.match(r'\s*\{\s*"id"\s*:\s*([1-9]\d*)\s*,\s*"messages"\s*:\s*\[', self.buffer)
            if not header:return []
            if int(header[1]) != self.scope.chat_id:
                raise CLIError('Telegram export is outside the approved group.')
            self.buffer = self.buffer[header.end():]
            self.header = True
        files = []
        decoder = json.JSONDecoder()
        while True:
            self.buffer = self.buffer.lstrip()
            if self.buffer.startswith(','):self.buffer = self.buffer[1:].lstrip()
            if not self.buffer or self.buffer.startswith(']'):break
            try:message, end = decoder.raw_decode(self.buffer)
            except json.JSONDecodeError:break  # This message has not been flushed in full yet.
            items = parse_export({'id': self.scope.chat_id, 'messages': [message]}, self.topic, scope=self.scope)
            if message['id'] in self.seen:
                raise CLIError('Telegram returned a duplicate message identity.')
            self.seen.add(message['id'])
            files.extend(items)
            self.buffer = self.buffer[end:]
        return files


def network_identity():
    """Use local route information only; no external speed-test service."""
    try:
        route = json.loads(subprocess.check_output(['ip', '-json', 'route', 'get', '1.1.1.1'], timeout=3))[0]
        identity = '/'.join(str(route.get(k, '')) for k in ('dev', 'gateway', 'prefsrc'))
        ipv6 = bool(subprocess.check_output(['ip', '-6', 'route', 'show', 'default'], timeout=3).strip())
        return hashlib.sha256(identity.encode()).hexdigest()[:16], ipv6
    except (OSError, ValueError, subprocess.SubprocessError):
        return 'unknown-network', False


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def completed_transfer_receipt(item, state=CLI_STATE):
    """Recognize a saved receipt without hashing its payload during queue scans.

    The download adapter still checks the full checksum before reuse.
    """
    directory = Path(state) / 'transfers' / str(item['source_message_id'])
    marker, output, proof = (directory / name for name in ('item.json', 'payload.part', 'complete.json'))
    try:
        if directory.is_symlink() or any(p.is_symlink() for p in (marker, output, proof)): return None
        receipt = read_json(proof, {})
        if not isinstance(receipt,dict):return None
        size = item['bytes_total']
        if (read_json(marker, None) == item and receipt.get('bytes') == receipt.get('total') == size
                and receipt.get('message_id') == item['source_message_id'] and receipt.get('filename') == item['filename']
                and re.fullmatch('[0-9a-f]{64}', receipt.get('sha256', '')) and output.stat().st_size == size):
            return receipt
    except (OSError, ValueError, TypeError): pass
    return None


def server_view(root=ROOT):
    return {**read_json(Path(root) / 'data/telegram-servers.json', {'endpoints': []}),
            'comparisons': read_json(Path(root) / 'data/telegram-server-speeds.json', {})}


def validate_servers(choices, root=ROOT):
    if not isinstance(choices, dict):
        raise ValueError('Invalid download server choices.')
    allowed = {e['key']: str(e['dc']) for e in server_view(root)['endpoints']}
    result = {}
    for dc, key in choices.items():
        if not re.fullmatch(r'[1-9]\d*', str(dc)) or not isinstance(key, str):
            raise ValueError('Invalid download server selection.')
        if key == 'auto':
            continue
        if allowed.get(key) != dc:
            raise ValueError('Choose an advertised server for this data center, or Auto.')
        result[dc] = key
    return result


class TelegramCLI:
    def __init__(self, root=ROOT, state=CLI_STATE, binary=None, config=None, service=None):
        self.root, self.state = Path(root), Path(state)
        self.source = load_source(self.root)
        self.binary = Path(binary) if binary else self.root / '.tools/tdl-stl/tdl'
        self.config = config or {}
        self.release_servers = {}
        self.use_service=(binary is None and read_json(self.root/'.tools/tdl-stl/build.json',{}).get('service_protocol')==1) if service is None else service
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.child = None
        self.lock = (self.state / 'application.lock').open('a')
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            self.close()
            raise CLIError('The Telegram CLI binary is unavailable. Rebuild it with tools/build-tdl.sh.')

    def command(self, *args):
        if args and args[0] == 'stl':
            args = ('stl', '--source', self.root / 'data/source.json', *args[1:])
        return [str(self.binary), '--storage', 'type=bolt,path=' + str(self.state / 'data'),
                '-n', 'telegram-stl-trial', '--reconnect-timeout', '30s', '--disable-progress-ps', *map(str, args)]

    def destinations(self):
        with tempfile.TemporaryDirectory(prefix='release-pad-',dir=self.state) as tmp:
            directory=Path(tmp);output=directory/'destinations.json'
            self._run(['stl','destinations','--output',output],directory,lambda:False,timeout=120)
            data=json.loads(output.read_text())
            rows=data.get('destinations')
            if not isinstance(rows,list):raise CLIError('Invalid Telegram destination list.')
            seen=set();result=[]
            for row in rows:
                if not isinstance(row,dict) or not re.fullmatch(r'-[1-9][0-9]{0,18}',str(row.get('id',''))) or row.get('kind') not in ('group','channel') or not isinstance(row.get('title'),str):
                    raise CLIError('Invalid Telegram destination metadata.')
                if row['id'] in seen:continue
                seen.add(row['id'])
                result.append({k:row.get(k,'') for k in ('id','title','kind','username')})
            return {'destinations':result}

    def close(self):
        self._terminate()
        if not self.lock.closed:
            self.lock.close()

    def _terminate(self):
        if self.child and self.child.poll() is None:
            os.killpg(self.child.pid, signal.SIGINT)
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.child.pid, signal.SIGKILL)
                self.child.wait(timeout=5)

    def _run(self, args, directory, stopped, tick=lambda: None, timeout=300, waiting=lambda: None):
        if args and args[0] in ('upload','up') and '--photo' not in args:
            paths=[Path(args[i+1]) for i,a in enumerate(args[:-1]) if a in ('--path','-p')]
            if not paths:raise CLIError('Choose an explicit release archive to upload.')
            for path in paths:
                if path.is_dir():raise CLIError('Choose release archives individually so their collage can be checked.')
                require_release_collage(path)
        if self.use_service and args and args[0]=='stl':
            from app.telegram_service import TelegramService, ServiceError
            try:return TelegramService(self.root,self.state,self.command).run(args,stopped,tick,timeout,waiting)
            except (ServiceError,OSError,ValueError) as error:raise CLIError(str(error)) from error
        # The CLI's Bolt storage needs exclusive access only while its command
        # runs. Release it during extraction/moves and between attachments so
        # organizer scans can share the existing login without another session.
        last=0
        while True:
            if stopped():raise CLIError('Stopped while waiting for the Telegram connection.')
            try:fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                now=time.monotonic()
                if now-last>=1:waiting();last=now
                time.sleep(.2)
        try:self._run_locked(args,directory,stopped,tick,timeout)
        finally:fcntl.flock(self.lock,fcntl.LOCK_UN)

    def _run_locked(self, args, directory, stopped, tick, timeout):
        started = time.monotonic()
        with (directory / 'command.log').open('w') as log:
            self.child = subprocess.Popen(self.command(*args), cwd=self.state, stdin=subprocess.DEVNULL,
                                          stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                while self.child.poll() is None:
                    if stopped():
                        raise CLIError('Stopped by request; completed staged files are retained. Incomplete transfers restart on retry.')
                    if time.monotonic() - started > timeout:
                        raise CLIError('Telegram CLI timed out. Retry the manual run.')
                    tick()
                    time.sleep(.2)
                tick()
                if stopped():
                    raise CLIError('Stopped by request; staged data retained.')
                if self.child.returncode != 0:
                    raise CLIError('Telegram CLI failed. Check the connection or CLI login, then retry.')
            finally:
                self._terminate()

    def _topic(self, topic):
        topic_id = self.source.topic_id(topic)
        catalog = read_json(self.root / 'data/creators.json', {}).get('creators', [])
        if topic_id is None or not any(c.get('topic_url') == topic and c.get('within_approved_group') is True for c in catalog):
            raise CLIError('Choose a creator from the approved Table of Contents.')
        return topic_id

    def list_files(self, topic, stopped=lambda: False, status=lambda message: None, on_files=None, *, incremental=False):
        topic_id = self._topic(topic)
        from app.topic_catalog import TopicCatalog
        catalog=TopicCatalog(self.root,self.source)
        saved=catalog.load(topic) if incremental else None
        after=saved['last_id'] if saved else 0
        started=time.monotonic()
        status('Checking messages since the last successful check…' if saved else 'Building the initial file catalog…' if incremental else 'Reading exact attachment names and sizes…')
        with tempfile.TemporaryDirectory(prefix='stl-scan-', dir=self.state) as temp:
            work = Path(temp); export = work / 'files.json'
            args = ['stl', 'files', '--topic', topic_id, '--output', export]
            if after:args.extend(['--after-id',after])
            last = 0
            partial = ExportProgress(topic, self.source) if on_files else None
            def heartbeat():
                nonlocal last
                if time.monotonic() - last > (1 if partial else 2):
                    last = time.monotonic()
                    if partial:
                        files = partial.read(export)
                        if files:
                            on_files(files)
                            return
                    status('Checking new messages…' if saved else 'Reading exact attachment names and sizes…')
            self._run(args, work, stopped, heartbeat, timeout=900,
                      waiting=lambda:status('Waiting for another Telegram scan or download to finish…'))
            if read_json(str(export)+'.complete',None)!={'complete':True,'topic':topic_id}:
                raise CLIError('Telegram did not confirm a complete topic scan; no downloads were planned.')
            if not export.is_file() or export.stat().st_size > 128 * 1024**2:
                raise CLIError('Telegram metadata export is missing or too large; scan not accepted.')
            try:
                items=parse_export(json.loads(export.read_text()), topic, scope=self.source)
                cursor=read_json(str(export)+'.cursor',None)
                if cursor is None:
                    if after:raise CLIError('Telegram did not confirm the incremental scan boundary.')
                    # Older CLI binaries can still serve complete previews;
                    # they cannot advance a persistent incremental cursor.
                    self.last_scan={'mode':'full','seconds':time.monotonic()-started,'new_files':len(items)}
                    return items
                if (cursor.get('topic')!=topic_id or cursor.get('after')!=after or type(cursor.get('last_id')) is not int
                        or cursor['last_id']<after):raise CLIError('Telegram returned an invalid incremental scan boundary.')
                if stopped():raise CLIError('Stopped before the scan was committed; its previous cursor was kept.')
                result=catalog.commit(topic,after,cursor['last_id'],items)
                self.last_scan={'mode':'incremental' if saved else 'full','seconds':time.monotonic()-started,'new_files':len(items),'catalog_files':len(result)}
                return result
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                raise CLIError('Telegram metadata could not be validated; no downloads were planned.') from error

    def refresh_servers(self, stopped=lambda: False):
        with tempfile.TemporaryDirectory(prefix='stl-servers-', dir=self.state) as temp:
            work = Path(temp); output = work / 'servers.json'
            self._run(['stl', 'servers', '--output', output], work, stopped, timeout=60)
            data = json.loads(output.read_text())
            if not data.get('endpoints'):
                raise CLIError('Telegram returned no download endpoints.')
            from app.subscription_store import SubscriptionStore
            data['refreshed_at'] = time.time()
            SubscriptionStore(self.root).atomic_write(self.root / 'data/telegram-servers.json', data)
            return data

    def download(self, item, progress, stopped, status=lambda event: None, probe_only=False, retest=False, quick_test=False):
        if probe_only:
            # Diagnostics must never remove a verified or partial real transfer.
            with tempfile.TemporaryDirectory(prefix='server-probe-', dir=self.state) as temporary:
                return self._download(item, progress, stopped, status, probe_only, retest, quick_test, Path(temporary))
        return self._download(item, progress, stopped, status, probe_only, retest, quick_test)

    def _download(self, item, progress, stopped, status, probe_only, retest, quick_test, directory=None):
        topic = self._topic(item['topic_url'])
        if (not safe_filename(item['filename']) or item['message_url'] != item['topic_url'] + '/' + str(item['source_message_id'])
                or item.get('unsafe_filename')):
            raise CLIError('Unsafe attachment filename or message identity.')
        size = item['bytes_total']
        directory = directory or self.state / 'transfers' / str(item['source_message_id'])
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink():
            raise CLIError('Invalid local transfer directory.')
        marker = directory / 'item.json'; output = directory / 'payload.part'; events = directory / 'events.jsonl'
        proof = directory / 'complete.json'
        if any(p.is_symlink() for p in (marker,output,events,proof)):
            raise CLIError('Invalid local transfer output.')
        if marker.exists() and read_json(marker, None) != item:
            raise CLIError('Local transfer metadata changed; its staged data needs review.')
        from app.subscription_store import SubscriptionStore
        store = SubscriptionStore(self.root)
        if output.is_file() and proof.is_file() and not probe_only:
            result = read_json(proof, {})
            from app.file_delivery import digest
            if result.get('bytes') == size and output.stat().st_size == size and digest(output) == result.get('sha256'):
                progress(size, size); return output
            raise CLIError('Previously completed local transfer failed verification.')
        if shutil.disk_usage(self.state).free < (0 if probe_only else size) + 1024**3:
            raise CLIError('Not enough local staging space for this file plus 1 GiB reserve.')
        if not marker.exists() and any(directory.iterdir()):
            raise CLIError('Unrecognized local transfer files need review.')
        store.atomic_write(marker, item)
        for path in (output, events, proof):
            if path.is_symlink():
                raise CLIError('Invalid local transfer output.')
            path.unlink(missing_ok=True)
        network, ipv6 = network_identity()
        server = self.config.get('download_servers', {}).get(str(item['dc_id']), 'auto')
        from app.release_rules import release_month
        month = release_month(item['filename'])
        release = (network, item['dc_id'], item['topic_url'], month)
        # Small files cannot provide a useful throughput sample. Test at the
        # first large attachment, once per artist/month/DC in this manual run.
        per_release = self.config.get('server_check_frequency', 'cached') == 'release'
        reuse = self.release_servers.get(release) if per_release and month and server == 'auto' and not probe_only else None
        select_release = bool(per_release and month and server == 'auto' and not probe_only and (reuse or size >= 64*1024*1024))
        if select_release:
            quick_test = True
            if not reuse: retest = True
        args = ['stl', 'download', '--topic', topic, '--message', item['source_message_id'],
                '--document-id', item['document_id'], '--dc', item['dc_id'], '--size', size,
                '--filename', item['filename'], '--output', output, '--events', events,
                '--server', server, '--network', network, '--cache', self.root / 'data/telegram-server-speeds.json']
        if ipv6: args.append('--ipv6')
        if probe_only: args.append('--probe-only')
        if retest: args.append('--retest')
        if quick_test: args.append('--quick-test')
        if reuse: args.extend(['--reuse-server', reuse])
        threshold = self.config.get('server_speed_threshold_mbps', 0)
        if threshold and server == 'auto' and not probe_only:
            args.extend(['--min-speed-mbps', threshold])
        offset = 0; pending = b''; complete = None; probed = False; previous = 0; active = False
        last_change = time.monotonic()
        def tick():
            nonlocal offset, pending, complete, probed, previous, last_change, active
            if events.exists():
                with events.open('rb') as stream:
                    stream.seek(offset); chunk = stream.read(); offset = stream.tell()
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    event = json.loads(line); kind = event.get('event')
                    if kind == 'error':
                        raise CLIError('Telegram: ' + str(event.get('message', 'transfer failed')))
                    if kind == 'download_start':
                        previous = 0; active = True; last_change = time.monotonic()
                        status(event); progress(0, size)
                    elif kind == 'download_resumed':
                        done = event.get('bytes')
                        if type(done) is not int or done < previous or done > size or event.get('total') != size:
                            raise CLIError('Invalid CLI resume progress; transfer stopped.')
                        previous = done; active = True; last_change = time.monotonic()
                        status(event); progress(done, size)
                    elif kind == 'progress':
                        done = event.get('bytes')
                        if type(done) is not int or done < previous or done > size or event.get('total') != size:
                            raise CLIError('Invalid CLI byte progress; transfer stopped.')
                        if done > previous: last_change = time.monotonic()
                        previous = done; progress(done, size)
                    elif kind == 'complete':
                        complete = event
                    elif kind == 'download_finished':
                        if event.get('bytes')!=size or event.get('total')!=size:
                            raise CLIError('CLI completion byte count is invalid.')
                        previous=size;last_change=time.monotonic();active=False
                        progress(size,size);status(event)
                    elif kind == 'probe_complete':
                        probed = True
                    else:
                        if kind == 'server_selected' and select_release:
                            self.release_servers[release] = event['endpoint']
                        if kind.startswith('server_'): active = False
                        last_change = time.monotonic(); status(event)
            if time.monotonic() - last_change > (300 if active else 180):
                raise CLIError('Telegram made no progress; retry to test the connection again.')
            if shutil.disk_usage(self.state).free < 256 * 1024**2:
                raise CLIError('Local staging is nearly full; transfer stopped.')
        def waiting():
            nonlocal last_change
            last_change=time.monotonic()
            status({'event':'waiting_for_telegram'})
        self._run(args, directory, stopped, tick, timeout=24*60*60,waiting=waiting)
        if probe_only:
            if not probed: raise CLIError('Server comparison did not finish.')
            return None
        if (not complete or complete.get('bytes') != size or complete.get('message_id') != item['source_message_id']
                or complete.get('filename') != item['filename'] or not re.fullmatch('[0-9a-f]{64}', complete.get('sha256', ''))
                or not output.is_file() or output.is_symlink() or output.stat().st_size != size):
            raise CLIError('CLI did not confirm a complete file; local data retained for retry.')
        store.atomic_write(proof, complete)
        progress(size, size)
        return output

    def cleanup_transfer(self, item):
        directory = self.state / 'transfers' / str(item['source_message_id'])
        if not directory.is_dir() or directory.is_symlink(): return
        if (directory / 'payload.part').exists(): return
        if any(p.name not in ('item.json', 'complete.json', 'events.jsonl', 'command.log') or p.is_symlink() for p in directory.iterdir()):
            return
        for p in directory.iterdir(): p.unlink()
        directory.rmdir()
