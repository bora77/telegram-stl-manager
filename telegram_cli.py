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
from source_scope import load_source

ROOT = Path(__file__).resolve().parent
# Preserve completed staging from the former Desktop worker across migration.
STATE = Path.home() / '.local/share/telegram-stl-desktop'
CLI_STATE = Path.home() / '.local/share/telegram-stl-tdl'


class CLIError(RuntimeError):
    pass


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
        files.append({'filename': name, 'source_message_id': mid,
                      'message_url': topic_url + '/' + str(mid), 'topic_url': topic_url,
                      'bytes_total': size, 'dc_id': dc, 'document_id': document_id,
                      'size_text': f'{size:,} bytes', 'unsafe_filename': not safe_filename(name)})
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
    def __init__(self, root=ROOT, state=CLI_STATE, binary=None, config=None):
        self.root, self.state = Path(root), Path(state)
        self.source = load_source(self.root)
        self.binary = Path(binary) if binary else self.root / '.tools/tdl-stl/tdl'
        self.config = config or {}
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.child = None
        self.lock = (self.state / 'application.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise CLIError('Another Telegram CLI operation is active. Wait for it to finish.')
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            self.close()
            raise CLIError('The Telegram CLI binary is unavailable. Rebuild it with tools/build-tdl.sh.')

    def command(self, *args):
        if args and args[0] == 'stl':
            args = ('stl', '--source', self.root / 'data/source.json', *args[1:])
        return [str(self.binary), '--storage', 'type=bolt,path=' + str(self.state / 'data'),
                '-n', 'telegram-stl-trial', '--reconnect-timeout', '30s', '--disable-progress-ps', *map(str, args)]

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

    def _run(self, args, directory, stopped, tick=lambda: None, timeout=300):
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

    def list_files(self, topic, stopped=lambda: False, status=lambda message: None, on_files=None):
        topic_id = self._topic(topic)
        with tempfile.TemporaryDirectory(prefix='stl-scan-', dir=self.state) as temp:
            work = Path(temp); export = work / 'files.json'
            args = ['stl', 'files', '--topic', topic_id, '--output', export]
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
                    status('Reading exact attachment names and sizes…')
            self._run(args, work, stopped, heartbeat, timeout=900)
            if read_json(str(export)+'.complete',None)!={'complete':True,'topic':topic_id}:
                raise CLIError('Telegram did not confirm a complete topic scan; no downloads were planned.')
            if not export.is_file() or export.stat().st_size > 128 * 1024**2:
                raise CLIError('Telegram metadata export is missing or too large; scan not accepted.')
            try:
                return parse_export(json.loads(export.read_text()), topic, scope=self.source)
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                raise CLIError('Telegram metadata could not be validated; no downloads were planned.') from error

    def refresh_servers(self, stopped=lambda: False):
        with tempfile.TemporaryDirectory(prefix='stl-servers-', dir=self.state) as temp:
            work = Path(temp); output = work / 'servers.json'
            self._run(['stl', 'servers', '--output', output], work, stopped, timeout=60)
            data = json.loads(output.read_text())
            if not data.get('endpoints'):
                raise CLIError('Telegram returned no download endpoints.')
            from subscription_store import SubscriptionStore
            data['refreshed_at'] = time.time()
            SubscriptionStore(self.root).atomic_write(self.root / 'data/telegram-servers.json', data)
            return data

    def download(self, item, progress, stopped, status=lambda event: None, probe_only=False, retest=False):
        topic = self._topic(item['topic_url'])
        if (not safe_filename(item['filename']) or item['message_url'] != item['topic_url'] + '/' + str(item['source_message_id'])
                or item.get('unsafe_filename')):
            raise CLIError('Unsafe attachment filename or message identity.')
        size = item['bytes_total']
        if shutil.disk_usage(self.state).free < (0 if probe_only else size) + 1024**3:
            raise CLIError('Not enough local staging space for this file plus 1 GiB reserve.')
        directory = self.state / 'transfers' / str(item['source_message_id'])
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink():
            raise CLIError('Invalid local transfer directory.')
        marker = directory / 'item.json'; output = directory / 'payload.part'; events = directory / 'events.jsonl'
        proof = directory / 'complete.json'
        if marker.exists() and read_json(marker, None) != item:
            raise CLIError('Local transfer metadata changed; its staged data needs review.')
        from subscription_store import SubscriptionStore
        store = SubscriptionStore(self.root)
        if output.is_file() and proof.is_file() and not probe_only:
            result = read_json(proof, {})
            from file_delivery import digest
            if result.get('bytes') == size and output.stat().st_size == size and digest(output) == result.get('sha256'):
                progress(size, size); return output
            raise CLIError('Previously completed local transfer failed verification.')
        if not marker.exists() and any(directory.iterdir()):
            raise CLIError('Unrecognized local transfer files need review.')
        store.atomic_write(marker, item)
        for path in (output, events, proof):
            if path.is_symlink():
                raise CLIError('Invalid local transfer output.')
            path.unlink(missing_ok=True)
        network, ipv6 = network_identity()
        server = self.config.get('download_servers', {}).get(str(item['dc_id']), 'auto')
        args = ['stl', 'download', '--topic', topic, '--message', item['source_message_id'],
                '--document-id', item['document_id'], '--dc', item['dc_id'], '--size', size,
                '--filename', item['filename'], '--output', output, '--events', events,
                '--server', server, '--network', network, '--cache', self.root / 'data/telegram-server-speeds.json']
        if ipv6: args.append('--ipv6')
        if probe_only: args.append('--probe-only')
        if retest: args.append('--retest')
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
                        if kind.startswith('server_'): active = False
                        last_change = time.monotonic(); status(event)
            if time.monotonic() - last_change > (300 if active else 180):
                raise CLIError('Telegram made no progress; retry to test the connection again.')
            if shutil.disk_usage(self.state).free < 256 * 1024**2:
                raise CLIError('Local staging is nearly full; transfer stopped.')
        self._run(args, directory, stopped, tick, timeout=24*60*60)
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
