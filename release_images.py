"""Extract image members locally without unpacking the release's model files."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import resource
import shutil
import stat
import subprocess
import tempfile
import time

IMAGES = {'.jpg', '.jpeg', '.jpe', '.png', '.webp', '.gif', '.bmp', '.tif', '.tiff',
          '.avif', '.heic', '.heif', '.svg', '.tga', '.dds', '.psd', '.ico', '.exr', '.hdr'}
ARCHIVES = {'.7z', '.zip', '.zipx', '.rar', '.tar', '.gz', '.bz2', '.xz', '.tgz', '.tbz2', '.txz'}
RESERVE = 1024 ** 3
MAX_LISTING = 64 * 1024 ** 2
BUNDLED_7ZIP = Path(__file__).resolve().parent / '.tools/7zip/runtime/usr/lib/7zip/7z'


class ExtractionError(RuntimeError):
    pass


def volume_key(name):
    """Group archive volumes without confusing a part number with a month."""
    match = re.fullmatch(r'(.+\.(?:7z|zip|rar))\.(\d{3,})', name, re.I)
    if match:return match[1].casefold(), int(match[2])
    match = re.fullmatch(r'(.+)[._ -]part(\d+)\.rar', name, re.I)
    if match:return (match[1] + '.rar').casefold(), int(match[2])
    match = re.fullmatch(r'(.+)\.([rz])(\d{2,})', name, re.I)
    if match:return (match[1] + ('.rar' if match[2].lower() == 'r' else '.zip')).casefold(), int(match[3]) + 1
    return name.casefold(), 0


def is_archive(name):
    return Path(name).suffix.lower() in ARCHIVES or volume_key(name)[1] > 0


def safe_component(name):
    clean = re.sub(r'[\\/<>:"|?*\x00-\x1f]', '_', name).strip().rstrip('.')
    if clean in ('', '.', '..'):clean = 'image'
    if len(clean.encode()) > 180:
        suffix = Path(clean).suffix[:12]
        clean = clean[:60] + suffix
    if clean != name:
        suffix = Path(clean).suffix
        clean = clean[:-len(suffix)] + '__' + hashlib.sha256(name.encode()).hexdigest()[:10] + suffix if suffix else clean + '__' + hashlib.sha256(name.encode()).hexdigest()[:10]
    return clean


def flat_image_name(archive, relative):
    """Stable names in one monthly image folder, including repeated basenames."""
    relative = PurePosixPath(str(relative))
    original = safe_component(relative.name)
    suffix = Path(original).suffix
    stem = original[:-len(suffix)] if suffix else original
    name = Path(archive).name
    # These separators were formerly treated as standalone archives. Keep the
    # original first-volume image key so repairs and later runs reuse its images.
    key = name.casefold() if re.fullmatch(r'(.+)[_ -]part\d+\.rar', name, re.I) else volume_key(name)[0]
    identity = key + '/' + str(relative)
    return stem[:100] + '__' + hashlib.sha256(identity.encode()).hexdigest()[:12] + suffix


def _limits():
    # Keep archive decoding from taking over the desktop or exhausting RAM.
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 ** 3, 2 * 1024 ** 3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 ** 3, 32 * 1024 ** 3))
    os.nice(10)


def command(args, work, stopped, tick=lambda: None, percent=None):
    if stopped():raise ExtractionError('Stopped by request; downloaded archives kept locally.')
    # Ubuntu's base 7zip package can list RAR files but needs the separate
    # 7zip-rar codec to decompress them. Prefer our matched local package pair.
    executable = str(BUNDLED_7ZIP) if BUNDLED_7ZIP.is_file() else shutil.which('7z') or shutil.which('7zz')
    if not executable:raise ExtractionError('7-Zip is required for image extraction; archive kept locally.')
    with tempfile.TemporaryFile(dir=work) as log:
        child = subprocess.Popen([executable, *args], stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=subprocess.STDOUT, env=dict(os.environ, LC_ALL='C.UTF-8'), preexec_fn=_limits)
        started = time.monotonic()
        offset = 0
        tail = b''
        def pulse():
            nonlocal offset, tail
            if percent is not None:
                data = os.pread(log.fileno(), MAX_LISTING + 1, offset)
                offset += len(data)
                if data:
                    values = re.findall(rb'(?<!\d)(\d{1,3})%', tail + data)
                    if values:percent(min(100, int(values[-1])))
                    tail = (tail + data)[-8:]
            tick()
        try:
            while child.poll() is None:
                if stopped():raise ExtractionError('Stopped by request; downloaded archives kept locally.')
                if time.monotonic() - started > 3600:raise ExtractionError('Image extraction exceeded one hour; archive kept locally.')
                if shutil.disk_usage(work).free < RESERVE:raise ExtractionError('Not enough local space for image extraction; archive kept locally.')
                if log.tell() > MAX_LISTING:raise ExtractionError('Archive listing is too large to process safely.')
                pulse()
                try:child.wait(timeout=.5)
                except subprocess.TimeoutExpired:pass
            log.seek(0)
            output = log.read(MAX_LISTING + 1)
            if len(output) > MAX_LISTING:raise ExtractionError('Archive listing is too large to process safely.')
            text = output.decode('utf-8', errors='strict')
            if child.returncode:
                raise ExtractionError('7-Zip could not read the complete archive (missing part, password, damage or unsupported format). Local files retained. ' + text[-700:].strip())
            pulse()
            return text
        finally:
            if child.poll() is None:
                child.kill();child.wait()


def listing(archive, work, stopped, tick=lambda: None):
    text = command(['l', '-slt', '-bd', '-p-', '--', str(archive)], work, stopped, tick)
    if '----------\n' not in text:raise ExtractionError('Archive contents could not be identified.')
    header, members = text.split('----------\n', 1)
    entries = [];seen = set()
    if not members.strip():return header,entries
    # Empty final values (for example RAR's "NT Security = ") include a
    # meaningful separator space. Strip only newlines, never field whitespace.
    for block in members.strip('\n').split('\n\n'):
        item = {}
        for line in block.splitlines():
            if ' = ' not in line:raise ExtractionError('Archive has an ambiguous member name or listing.')
            key, value = line.split(' = ', 1)
            if key in item:raise ExtractionError('Archive listing contains ambiguous metadata.')
            item[key] = value
        name = item.get('Path', '')
        path = PurePosixPath(name)
        if not name or '\\' in name or path.is_absolute() or any(p in ('', '.', '..') for p in name.split('/')) or re.search(r'[:\x00-\x1f]', name):
            raise ExtractionError('Archive contains an unsafe member path; local archive retained.')
        attributes = item.get('Attributes', '').split()
        if any(v.startswith('l') for v in attributes) or any('Link' in k and v for k, v in item.items()) or item.get('Anti') == '+':
            raise ExtractionError('Archive contains links or special entries; review required.')
        if item.get('Folder') == '+' or any(v.startswith('D') or v.startswith('d') for v in attributes):continue
        if name.casefold() in seen:raise ExtractionError('Archive contains duplicate image paths or file names.')
        seen.add(name.casefold())
        if item.get('Encrypted') == '+':raise ExtractionError('Password-protected release needs review; local archive retained.')
        try:size = int(item['Size'])
        except (KeyError, ValueError):raise ExtractionError('Archive member size could not be read.')
        if size < 0:raise ExtractionError('Invalid archive member size.')
        entries.append({'name': name, 'size': size})
    return header, entries


def complete_split_7z(archive, header):
    """Check split 7z volume lengths against its parsed header without decoding models.

    7-Zip has already opened the archive and validated its headers. Selected
    members still receive CRC checks during extraction. Other multivolume
    formats retain the full test until their completeness rules are supported.
    """
    blocks = [dict(line.split(' = ', 1) for line in block.splitlines() if ' = ' in line)
              for block in re.split(r'\n-{2,}\n', header)]
    split = next((b for b in blocks if b.get('Type') == 'Split'), None)
    inner = next((b for b in blocks if b.get('Type') == '7z'), None)
    match = re.fullmatch(r'(.+\.7z\.)(0+1)', archive.name, re.I)
    if not split or not inner or not match:return False
    try:
        count = int(split['Volumes'])
        expected = int(split['Total Physical Size'])
        physical = int(inner['Physical Size'])
    except (KeyError, ValueError):return False
    if not 1 <= count <= 50000 or expected <= 0 or expected != physical:
        raise ExtractionError('Split archive lengths do not match its header; original parts retained.')
    volumes = {}
    for path in archive.parent.iterdir():
        if not path.name.startswith(match[1]):continue
        suffix = path.name[len(match[1]):]
        if not re.fullmatch(r'\d{3,}', suffix):continue
        index = int(suffix)
        if index in volumes or path.is_symlink() or not path.is_file():
            raise ExtractionError('Ambiguous split archive parts; originals retained.')
        volumes[index] = path.stat().st_size
    if set(volumes) != set(range(1, count + 1)) or any(size <= 0 for size in volumes.values()) or sum(volumes.values()) != expected:
        raise ExtractionError('Split archive is missing or has changed parts; originals retained.')
    return True


def extract_images(archive, work, progress=lambda *args: None, stopped=lambda: False):
    """Return extracted image paths. Originals are never modified or deleted."""
    archive = Path(archive).resolve(strict=True);work = Path(work)
    output = work / 'output';output.mkdir(parents=True, exist_ok=True)
    results = []

    def unpack(source, prefix=Path(), depth=0):
        if depth > 6:raise ExtractionError('Nested archive depth needs review; local archive retained.')
        progress('listing', len(results), None, 0, None)
        header, entries = listing(source, work, stopped, lambda: progress('listing', len(results), None, 0, None))
        selected = [e for e in entries if Path(e['name']).suffix.lower() in IMAGES or is_archive(e['name'])]
        total = sum(e['size'] for e in selected)
        if len(selected) > 50000 or shutil.disk_usage(work).free < total + RESERVE:
            raise ExtractionError('Not enough local capacity for release images; archive retained.')
        if 'Type = Split' in header or 'Multivolume = +' in header:
            checked = None
            def percent(value):
                nonlocal checked
                checked = value
            def checking():
                progress('checking_parts', len(results), None, checked or 0, 100 if checked is not None else None)
            checking()
            if not complete_split_7z(source, header):
                command(['t', '-bsp1', '-bb0', '-mmt=2', '-p-', '--', str(source)], work, stopped, checking, percent)
            progress('checking_parts', len(results), None, 100, 100)
        if not selected:return
        with tempfile.TemporaryDirectory(prefix='members-', dir=work) as temporary:
            temporary = Path(temporary);members = temporary / 'members';members.mkdir()
            include = temporary / 'include.txt'
            include.write_text(''.join(e['name'] + '\n' for e in selected))
            image_entries = [e for e in selected if Path(e['name']).suffix.lower() in IMAGES]
            def tick():
                done = 0;count = 0
                for entry in selected:
                    path = members / entry['name']
                    if path.is_symlink():raise ExtractionError('Extracted link refused.')
                    if path.is_file():
                        size = path.stat().st_size
                        if size > entry['size']:raise ExtractionError('Extracted member exceeds its listed size.')
                        done += size
                        if entry in image_entries and size == entry['size']:count += 1
                progress('extracting', count, len(image_entries), done, total)
            command(['x', '-bd', '-bb0', '-mmt=2', '-y', '-p-', '-spd', '-scsUTF-8',
                     '-o' + str(members), '-i@' + str(include), '--', str(source)], work, stopped, tick)
            expected = {e['name']: e for e in selected}
            actual = {}
            for path in members.rglob('*'):
                mode = path.lstat().st_mode
                if stat.S_ISDIR(mode):continue
                if not stat.S_ISREG(mode):raise ExtractionError('Extracted special file refused.')
                actual[str(path.relative_to(members))] = path.stat().st_size
            if actual != {e['name']: e['size'] for e in selected}:
                raise ExtractionError('Extracted members do not match the archive listing.')
            for entry in image_entries:
                relative = prefix / Path(*(safe_component(p) for p in PurePosixPath(entry['name']).parts))
                target = output / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():raise ExtractionError('Image names collide; review required.')
                os.rename(members / entry['name'], target);results.append(target)
            nested = {}
            for entry in selected:
                if is_archive(entry['name']):
                    nested.setdefault(volume_key(entry['name'])[0], []).append(entry['name'])
            for names in nested.values():
                first = min(names, key=lambda name: volume_key(name)[1])
                nested_prefix = prefix / Path(*(safe_component(p) for p in PurePosixPath(first).parts))
                unpack(members / first, nested_prefix, depth + 1)
    if is_archive(archive.name):
        unpack(archive)
    elif archive.suffix.lower() in IMAGES:
        target = output / safe_component(archive.name)
        shutil.copyfile(archive, target);results.append(target)
    progress('complete', len(results), len(results), sum(p.stat().st_size for p in results), sum(p.stat().st_size for p in results))
    return sorted(results)
