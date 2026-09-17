"""Extract image members locally without unpacking the release's model files."""
from contextlib import contextmanager
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
import zipfile
import zlib
import lzma

IMAGES = {'.jpg', '.jpeg', '.jpe', '.png', '.webp', '.gif', '.bmp', '.tif', '.tiff',
          '.avif', '.heic', '.heif', '.svg', '.tga', '.dds', '.psd', '.ico', '.exr', '.hdr'}
IMAGE_ATTACHMENTS = IMAGES | {'.jfif', '.jxl', '.apng', '.svgz', '.heics', '.heifs'}
ARCHIVES = {'.7z', '.zip', '.zipx', '.rar', '.tar', '.gz', '.bz2', '.xz', '.tgz', '.tbz2', '.txz'}
RESERVE = 1024 ** 3
MAX_LISTING = 64 * 1024 ** 2
BUNDLED_7ZIP = Path(__file__).resolve().parent / '.tools/7zip/runtime/usr/lib/7zip/7z'
_REPR_STRING = r'''(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")'''
_SAFE_NAME_NOTICE = re.compile(r'.+: extracted '+_REPR_STRING+r' using safe name '+_REPR_STRING+r'\.')


def image_warnings(values):
    """Keep extraction problems, excluding historical successful name repairs."""
    return [value for value in (values or ()) if not _SAFE_NAME_NOTICE.fullmatch(value)]


def is_image_attachment(item):
    """Exclude direct Telegram image files without opening their contents."""
    name=item.get('filename','');mime=item.get('mime_type','')
    return (isinstance(name,str) and Path(name).suffix.casefold() in IMAGE_ATTACHMENTS
            or isinstance(mime,str) and mime.strip().casefold().startswith('image/'))


class ExtractionError(RuntimeError):
    pass


class ArchiveReadError(ExtractionError):
    def __init__(self, archive, output):
        self.archive=Path(archive);self.output=output
        super().__init__('7-Zip could not read the complete archive (missing part, password, damage or unsupported format). Local files retained. '+output[-700:].strip())


class MissingVolumeError(ExtractionError):
    def __init__(self, archive, missing):
        self.archive=Path(archive);self.missing=missing
        super().__init__('Missing archive volume: '+missing+'. Downloaded parts were kept locally.')


def archive_error(text, archive):
    header=text.split('----------\n',1)[0]
    match=re.search(r'^ERROR = Missing volume : ([^\r\n]+)$',header,re.M)
    if match and match[1] not in ('.','..') and not re.search(r'[\\/<>:"|?*\x00-\x1f]',match[1]):
        return MissingVolumeError(archive,match[1])
    return ArchiveReadError(archive,text)


def volume_key(name):
    """Group archive volumes without confusing a part number with a month."""
    match = re.fullmatch(r'(.+\.(?:7z|zip|rar))\.(\d{3,})', name, re.I)
    if match:return Path(match[1]).stem.casefold(), int(match[2])
    match = re.fullmatch(r'(.+)[._ -]part(\d+)\.rar', name, re.I)
    if match:return (match[1] + '.rar').casefold(), int(match[2])
    match = re.fullmatch(r'(.+)\.([rz])(\d{2,})', name, re.I)
    if match:return (match[1] + ('.rar' if match[2].lower() == 'r' else '.zip')).casefold(), int(match[3]) + 1
    match = re.fullmatch(r'(.+)\.(\d{3,})', name, re.I)
    if match:return match[1].casefold(),int(match[2])
    return name.casefold(), 0


@contextmanager
def split_input_aliases(source, work):
    """Give mixed numbered volumes consistent temporary names, never rename originals."""
    match=re.fullmatch(r'(.+)\.(0+1)',source.name)
    if not match:
        yield source,False
        return
    key,_=volume_key(source.name)
    parts={}
    for path in source.parent.iterdir():
        if not re.fullmatch(r'.+\.\d{3,}',path.name) or volume_key(path.name)[0]!=key:continue
        index=volume_key(path.name)[1]
        if index in parts:raise ExtractionError('Ambiguous split archive parts; originals retained.')
        if path.is_symlink() or not path.is_file():raise ExtractionError('Archive volume is not a regular file.')
        parts[index]=path
    expected=lambda index:match[1]+'.'+str(index).zfill(len(match[2]))
    if all(path.name==expected(index) for index,path in parts.items()):
        yield source,False
        return
    with tempfile.TemporaryDirectory(prefix='volume-aliases-',dir=work) as directory:
        directory=Path(directory)
        for index,path in parts.items():(directory/expected(index)).symlink_to(path.resolve(strict=True))
        yield directory/source.name,True


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


def mapped_member_name(name):
    """Keep ordinary paths; give unusual names a confined, deterministic alias.

    Archive names are selectors only when an alias is needed. They are never
    passed to an extractor that can create filesystem paths.
    """
    if not name or re.search(r'[\x00-\x1f]', name):
        raise ExtractionError('Archive member name cannot be read unambiguously: '+repr(name))
    parts=name.replace('\\','/').split('/')
    if (not any(p in ('','.','..') for p in parts) and not re.search(r'[\\<>:"|?*]',name)
            and all(len(p.encode())<=180 and p==p.strip().rstrip('.') for p in parts)):
        return name
    basename=next((p for p in reversed(parts) if p not in ('','.','..')),'image')
    # Keep multipart suffixes together: changing each part's basename hash
    # separately would prevent the archive reader from finding its companions.
    match=re.fullmatch(r'(.+)(\.(?:7z|zip|rar)\.\d{3,}|[._ -]part\d+\.rar|\.[rz]\d{2,}|\.\d{3,})',basename,re.I)
    clean=safe_component(match[1])+match[2] if match else safe_component(basename)
    identity=volume_key(name)[0] if volume_key(name)[1] else name
    return '__renamed_'+hashlib.sha256(identity.encode()).hexdigest()[:16]+'/'+clean


def member_path(members, entry):
    return members / entry.get('disk_name',entry['name'])


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


def command(args, work, stopped, tick=lambda: None, percent=None, *, executable=None, timeout=3600, cwd=None):
    if stopped():raise ExtractionError('Stopped by request; downloaded archives kept locally.')
    encoding_args=[] if executable else ['-sccUTF-8']
    # Ubuntu's base 7zip package can list RAR files but needs the separate
    # 7zip-rar codec to decompress them. Prefer our matched local package pair.
    executable = executable or (str(BUNDLED_7ZIP) if BUNDLED_7ZIP.is_file() else shutil.which('7z') or shutil.which('7zz'))
    if not executable:raise ExtractionError('7-Zip is required for image extraction; archive kept locally.')
    with tempfile.TemporaryFile(dir=work) as log:
        child = subprocess.Popen([executable, *encoding_args, *args], stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=subprocess.STDOUT, env=dict(os.environ, LC_ALL='C.UTF-8'), preexec_fn=_limits, cwd=cwd)
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
                if time.monotonic() - started > timeout:raise ExtractionError('Image extraction exceeded its time limit; archive kept locally.')
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
                raise archive_error(text,args[-1])
            pulse()
            return text
        finally:
            if child.poll() is None:
                child.kill();child.wait()


def listing(archive, work, stopped, tick=lambda: None):
    native_zip=False
    try:text = command(['l', '-slt', '-bd', '-p-', '--', str(archive)], work, stopped, tick)
    except UnicodeDecodeError:
        # Some ZIPs store legacy filename bytes which 7-Zip prints unchanged.
        # Read their central directory using the ZIP encoding rules, and stream
        # selected members by exact identity instead of lossy text selectors.
        if not zipfile.is_zipfile(archive):raise ExtractionError('Archive listing has unreadable filename encoding; local archive retained.')
        native_zip=True;blocks=[]
        with zipfile.ZipFile(archive) as source:
            for info in source.infolist():
                name=info.orig_filename;mode=(info.external_attr>>16)&0xffff
                if re.search(r'[\x00-\x1f]',name):raise ExtractionError('Archive member name cannot be read unambiguously: '+repr(name))
                special=stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR)
                blocks.append(f"Path = {name}\nSize = {info.file_size}\nFolder = {'+' if info.is_dir() else '-'}\nEncrypted = {'+' if info.flag_bits&1 else '-'}\nAttributes = {'l' if special else ''}")
        text='Type = zip\n----------\n'+'\n\n'.join(blocks)
    if '----------\n' not in text:raise ExtractionError('Archive contents could not be identified.')
    header, members = text.split('----------\n', 1)
    # 7-Zip can successfully detect an archive whose extension is wrong. Its
    # trailing warning count is a report footer, not another archive member.
    if re.search(r'^Open WARNING: Cannot open the file as \[[^\]\r\n]+\] archive$', header, re.M):
        members = re.sub(r'\n{2,}Warnings: [1-9]\d*\n\Z', '\n\n', members)
    entries = [];seen = set()
    if not members.strip():return header,entries
    # Empty final values (for example RAR's "NT Security = ") include a
    # meaningful separator space. Strip only newlines, never field whitespace.
    for block in members.strip('\n').split('\n\n'):
        item = {}
        for line in block.split('\n'):
            if ' = ' not in line:raise ExtractionError('Archive has an ambiguous member name or listing.')
            key, value = line.split(' = ', 1)
            if key in item:raise ExtractionError('Archive listing contains ambiguous metadata.')
            item[key] = value
        name = item.get('Path', '')
        parts=name.replace('\\','/').split('/')
        if '__MACOSX' in parts or any(part.startswith('._') for part in parts):continue
        attributes = item.get('Attributes', '').split()
        if any(v.startswith('l') for v in attributes) or any('Link' in k and v for k, v in item.items()) or item.get('Anti') == '+':
            raise ExtractionError(archive.name+': link or special entry requires review: '+repr(name))
        # Directory markers (including ./ and trailing slashes) are not files
        # to extract. Only selected regular members create output directories.
        if item.get('Folder') == '+' or any(v.startswith('D') or v.startswith('d') for v in attributes):continue
        try:disk_name=mapped_member_name(name)
        except ExtractionError as error:raise ExtractionError(archive.name+': '+str(error)) from error
        if name.casefold() in seen:raise ExtractionError('Archive contains duplicate image paths or file names.')
        seen.add(name.casefold())
        if item.get('Encrypted') == '+':raise ExtractionError('Password-protected release needs review; local archive retained.')
        try:size = int(item['Size'])
        except (KeyError, ValueError):raise ExtractionError('Archive member size could not be read.')
        if size < 0:raise ExtractionError('Invalid archive member size.')
        entry={'name': name, 'size': size}
        if disk_name!=name or native_zip:entry['disk_name']=disk_name
        entries.append(entry)
    return header, entries


def complete_split_7z(archive, header, *, internal_aliases=False):
    """Check split 7z volume lengths against its parsed header without decoding models.

    7-Zip has already opened the archive and validated its headers. Selected
    members still receive CRC checks during extraction. Other multivolume
    formats retain the full test until their completeness rules are supported.
    """
    blocks = [dict(line.split(' = ', 1) for line in block.splitlines() if ' = ' in line)
              for block in re.split(r'\n-{2,}\n', header)]
    split = next((b for b in blocks if b.get('Type') == 'Split'), None)
    inner = next((b for b in blocks if b.get('Type') == '7z'), None)
    match = re.fullmatch(r'(.+\.)(0+1)', archive.name, re.I)
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
        if index in volumes or (path.is_symlink() and not internal_aliases) or not path.is_file():
            raise ExtractionError('Ambiguous split archive parts; originals retained.')
        volumes[index] = path.stat().st_size
    if set(volumes) != set(range(1, count + 1)) or any(size <= 0 for size in volumes.values()) or sum(volumes.values()) != expected:
        raise ExtractionError('Split archive is missing or has changed parts; originals retained.')
    return True


def recover_members(source, members, selected, work, stopped, progress, warn, error):
    """Retry members independently; only publish ones whose decoding/CRC passes."""
    shutil.rmtree(members);members.mkdir()
    kept=[]
    def check():
        if stopped():raise ExtractionError('Stopped by request; originals retained.')
        if shutil.disk_usage(work).free<RESERVE:raise ExtractionError('Not enough local space for image recovery.')
        progress(len(kept),len(selected))
    if zipfile.is_zipfile(source):
        candidate=source
        repair=shutil.which('zip')
        if repair and re.search(r'^ERROR: Headers Error',error.output,re.M):
            candidate=work/'repaired.zip'
            if shutil.disk_usage(work).free<source.stat().st_size+RESERVE:
                raise ExtractionError('Not enough local space to recover the ZIP index.')
            try:
                command(['-FF',str(source),'--out',str(candidate)],work,stopped,check,executable=repair,timeout=60)
                warn(source.name+': recovered the damaged ZIP index in a temporary local copy.')
            except ArchiveReadError:
                candidate=source
        try:archive=zipfile.ZipFile(candidate)
        except (zipfile.BadZipFile,NotImplementedError):
            warn(source.name+': image archive could not be read; its original was retained.')
            return kept
        with archive:
            infos=archive.infolist();by_name={}
            for info in infos:by_name.setdefault(info.filename,[]).append(info)
            for entry in selected:
                check();name=entry['name'];target=members/name
                matches=by_name.get(name,[])
                if len(matches)!=1:
                    warn(source.name+' / '+name+': skipped; image entry is missing or ambiguous.')
                    continue
                info=matches[0];mode=(info.external_attr>>16)&0xffff
                if info.orig_filename!=name or stat.S_IFMT(mode) not in (0,stat.S_IFREG) or info.is_dir():
                    raise ExtractionError('Recovered archive contains a link or special entry.')
                if info.file_size<0 or info.file_size>32*1024**3 or shutil.disk_usage(work).free<info.file_size+RESERVE:
                    raise ExtractionError('Not enough local capacity for a recovered image.')
                target.parent.mkdir(parents=True,exist_ok=True)
                try:
                    with archive.open(info) as incoming,target.open('xb') as out:
                        done=0
                        while block:=incoming.read(1024*1024):
                            check();done+=len(block)
                            if done>info.file_size:raise zipfile.BadZipFile('Member exceeds its declared size')
                            out.write(block)
                        if done!=info.file_size:raise zipfile.BadZipFile('Incomplete member')
                    kept.append({'name':name,'size':done})
                except (zipfile.BadZipFile,zlib.error,lzma.LZMAError,EOFError,NotImplementedError,RuntimeError) as problem:
                    if isinstance(problem,ExtractionError):raise
                    target.unlink(missing_ok=True)
                    warn(source.name+' / '+name+': skipped; image data could not be decoded or failed its checksum.')
    else:
        include=work/'recover-include.txt'
        for entry in selected:
            check();include.write_text(entry['name']+'\n')
            try:
                command(['x','-bd','-bb0','-mmt=2','-y','-p-','-spd','-scsUTF-8',
                         '-o'+str(members),'-i@'+str(include),'--',str(source)],work,stopped,check)
                kept.append(entry)
            except ArchiveReadError:
                target=members/entry['name']
                if target.is_file() and not target.is_symlink():target.unlink()
                warn(source.name+' / '+entry['name']+': skipped; image data could not be decoded or failed its checksum.')
    check()
    return kept


def extract_mapped_member(source, entry, target, work, stopped, tick):
    """Decode one exact member into a filename chosen by us, never by 7-Zip."""
    started=time.monotonic()
    def check():
        if stopped():raise ExtractionError('Stopped by request; downloaded archives kept locally.')
        if time.monotonic()-started>3600:raise ExtractionError('Renamed member extraction exceeded its time limit.')
        if shutil.disk_usage(work).free<RESERVE:raise ExtractionError('Not enough local space for renamed archive members.')
        if target.exists() and target.stat().st_size>entry['size']:raise ExtractionError('Renamed member exceeds its listed size.')
        tick()
    if entry['size']>32*1024**3:raise ExtractionError('Renamed archive member exceeds the local extraction limit.')
    check();target.parent.mkdir(parents=True,exist_ok=True)
    try:
        native=False
        if zipfile.is_zipfile(source):
            try:
                with zipfile.ZipFile(source) as archive:
                    info=archive.getinfo(entry['name']);mode=(info.external_attr>>16)&0xffff
                    if info.orig_filename!=entry['name'] or info.is_dir() or stat.S_IFMT(mode) not in (0,stat.S_IFREG):
                        raise ExtractionError('Renamed archive member is a link or special entry: '+repr(entry['name']))
                    if info.file_size!=entry['size']:raise ExtractionError('Renamed member differs from its listed size.')
                    if info.compress_type in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED,zipfile.ZIP_BZIP2,zipfile.ZIP_LZMA):
                        with archive.open(info) as incoming,target.open('xb') as out:
                            done=0
                            while block:=incoming.read(1024*1024):
                                check();done+=len(block)
                                if done>entry['size']:raise ExtractionError('Renamed member exceeds its listed size.')
                                out.write(block)
                        native=True
            except (KeyError,zipfile.BadZipFile,zlib.error,lzma.LZMAError,EOFError,NotImplementedError,RuntimeError) as error:
                if isinstance(error,ExtractionError):raise
                raise ArchiveReadError(source,'Renamed member could not be decoded or failed its checksum: '+repr(entry['name'])) from error
        if not native:
            executable=str(BUNDLED_7ZIP) if BUNDLED_7ZIP.is_file() else shutil.which('7z') or shutil.which('7zz')
            if not executable:raise ExtractionError('7-Zip is required for renamed archive members.')
            include=work/'renamed-include.txt';include.write_text(entry['name']+'\n')
            with target.open('xb') as out,tempfile.TemporaryFile(dir=work) as log:
                child=subprocess.Popen([executable,'x','-so','-bso0','-bse2','-bsp0','-mmt=2','-p-','-spd',
                    '-scsUTF-8','-i@'+str(include),'--',str(source)],stdin=subprocess.DEVNULL,stdout=out,stderr=log,
                    env=dict(os.environ,LC_ALL='C.UTF-8'),preexec_fn=_limits)
                try:
                    while child.poll() is None:
                        check()
                        if log.tell()>MAX_LISTING:raise ExtractionError('Renamed member diagnostic output is too large.')
                        try:child.wait(timeout=.5)
                        except subprocess.TimeoutExpired:pass
                    if child.returncode:
                        log.seek(0);raise ArchiveReadError(source,log.read(MAX_LISTING).decode('utf-8',errors='replace'))
                finally:
                    if child.poll() is None:child.kill();child.wait()
        check()
        if target.stat().st_size!=entry['size']:raise ExtractionError('Renamed member differs from its listed size: '+repr(entry['name']))
    except Exception:
        target.unlink(missing_ok=True)
        raise


def extract_images(archive, work, progress=lambda *args: None, stopped=lambda: False, *, warnings=None, name_changes=None):
    """Return extracted image paths. Originals are never modified or deleted."""
    archive = Path(archive).resolve(strict=True);work = Path(work)
    output = work / 'output';output.mkdir(parents=True, exist_ok=True)
    results = []
    def warn(message):
        if warnings is not None and message not in warnings:warnings.append(message)

    def unpack(source, prefix=Path(), depth=0):
        with split_input_aliases(source,work) as (aliased,internal_aliases):
            unpack_members(aliased,prefix,depth,internal_aliases)

    def unpack_members(source, prefix, depth, internal_aliases):
        if depth > 6:raise ExtractionError('Nested archive depth needs review; local archive retained.')
        progress('listing', len(results), None, 0, None)
        try:header, entries = listing(source, work, stopped, lambda: progress('listing', len(results), None, 0, None))
        except ArchiveReadError:
            if warnings is None:raise
            warn((str(prefix) if prefix.parts else source.name)+': images could not be listed; original archive retained.')
            return
        selected = [e for e in entries if Path(e['name']).suffix.lower() in IMAGES or is_archive(e['name'])]
        disk_names=[e.get('disk_name',e['name']).casefold() for e in selected]
        if len(set(disk_names))!=len(disk_names):raise ExtractionError('Renamed archive members would collide; originals retained.')
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
            if not complete_split_7z(source, header, internal_aliases=internal_aliases):
                try:command(['t', '-bsp1', '-bb0', '-mmt=2', '-p-', '--', str(source)], work, stopped, checking, percent)
                except ArchiveReadError:
                    if warnings is None:raise
                    warn(source.name+': archive integrity check found damage; keeping only verifiable images.')
            progress('checking_parts', len(results), None, 100, 100)
        if not selected:return
        with tempfile.TemporaryDirectory(prefix='members-', dir=work) as temporary:
            temporary = Path(temporary);members = temporary / 'members';members.mkdir()
            include = temporary / 'include.txt'
            ordinary=[e for e in selected if 'disk_name' not in e]
            renamed=[e for e in selected if 'disk_name' in e]
            include.write_text(''.join(e['name'] + '\n' for e in ordinary))
            image_entries = [e for e in selected if Path(e['name']).suffix.lower() in IMAGES]
            def tick():
                done = 0;count = 0
                for entry in selected:
                    path = member_path(members,entry)
                    if path.is_symlink():raise ExtractionError('Extracted link refused.')
                    if path.is_file():
                        size = path.stat().st_size
                        if size > entry['size']:raise ExtractionError('Extracted member exceeds its listed size.')
                        done += size
                        if entry in image_entries and size == entry['size']:count += 1
                progress('extracting', count, len(image_entries), done, total)
            try:
                if ordinary:
                    command(['x', '-bd', '-bb0', '-mmt=2', '-y', '-p-', '-spd', '-scsUTF-8',
                             '-o' + str(members), '-i@' + str(include), '--', str(source)], work, stopped, tick)
            except ArchiveReadError as error:
                if warnings is None:raise
                ordinary=recover_members(source,members,ordinary,temporary,stopped,
                    lambda count,total:progress('recovering',count,total,0,None),warn,error)
            selected=ordinary+renamed
            kept=[]
            for entry in renamed:
                try:extract_mapped_member(source,entry,member_path(members,entry),temporary,stopped,tick)
                except ArchiveReadError:
                    if warnings is None:raise
                    warn(source.name+' / '+repr(entry['name'])+': skipped; renamed member could not be decoded or failed its checksum.')
                    continue
                kept.append(entry)
                message=source.name+': extracted '+repr(entry['name'])+' using safe name '+repr(entry['disk_name'])+'.'
                if name_changes is not None and message not in name_changes:name_changes.append(message)
            selected=ordinary+kept
            image_entries=[e for e in selected if Path(e['name']).suffix.lower() in IMAGES]
            actual = {}
            for path in members.rglob('*'):
                mode = path.lstat().st_mode
                if stat.S_ISDIR(mode):continue
                if not stat.S_ISREG(mode):raise ExtractionError('Extracted special file refused.')
                actual[str(path.relative_to(members))] = path.stat().st_size
            if actual != {e.get('disk_name',e['name']): e['size'] for e in selected}:
                raise ExtractionError('Extracted members do not match the archive listing.')
            for entry in image_entries:
                relative = prefix / Path(*(safe_component(p) for p in PurePosixPath(entry.get('disk_name',entry['name'])).parts))
                target = output / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():raise ExtractionError('Image names collide; review required.')
                os.rename(member_path(members,entry), target);results.append(target)
            nested = {}
            for entry in selected:
                if is_archive(entry['name']):
                    nested.setdefault(volume_key(entry['name'])[0], []).append(entry)
            for group in nested.values():
                first = min(group, key=lambda entry: volume_key(entry['name'])[1])
                nested_prefix = prefix / Path(*(safe_component(p) for p in PurePosixPath(first.get('disk_name',first['name'])).parts))
                unpack(member_path(members,first), nested_prefix, depth + 1)
    if is_archive(archive.name):
        unpack(archive)
    elif archive.suffix.lower() in IMAGES:
        target = output / safe_component(archive.name)
        shutil.copyfile(archive, target);results.append(target)
    progress('complete', len(results), len(results), sum(p.stat().st_size for p in results), sum(p.stat().st_size for p in results))
    return sorted(results)
