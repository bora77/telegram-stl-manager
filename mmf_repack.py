"""Verified local MMF repackaging, preserving member names and bounding volumes."""
import json
from pathlib import Path
import re
import shutil
import tempfile
from file_delivery import digest
from release_images import (command, listing, safe_component, member_path,
    split_input_aliases, extract_mapped_member, ExtractionError, RESERVE)

VOLUME_BYTES=4000*1024*1024  # 7-Zip's 4000M (MiB) volume size.
COMPRESSION_LEVEL=7
POLICY_VERSION=5


def is_metadata_file(name):
    """Known OS-generated metadata only; never discard arbitrary hidden files."""
    parts=name.replace('\\','/').split('/')
    directories={'__macosx','.spotlight-v100','.trashes','.fseventsd','.temporaryitems','$recycle.bin'}
    filenames={'.ds_store','thumbs.db','ehthumbs.db','desktop.ini'}
    return (any(part.casefold() in directories for part in parts)
            or parts[-1].casefold() in filenames or parts[-1].startswith('._'))


def archive_name(filename):
    stem=re.sub(r'(?i)(\.part\d+\.rar|\.(?:7z|zip|rar)\.\d{3,}|\.tar\.(?:gz|bz2|xz)|\.(?:7z|zip|zipx|rar|tar|gz|bz2|xz|tgz|tbz2|txz))$','',filename)
    return safe_component(stem)+'.7z'


def repack(source,work,identity,progress=lambda *args:None,stopped=lambda:False,*,volume_bytes=VOLUME_BYTES,sources=None,release_name=None):
    source=Path(source).resolve(strict=True);work=Path(work).resolve();work.mkdir(parents=True,exist_ok=True)
    receipt=work/'repack.json';name=safe_component(release_name)+'.7z' if release_name else archive_name(source.name)
    sources=sources or [{'path':source,'folder':''}]
    if receipt.exists():
        saved=json.loads(receipt.read_text())
        if saved.get('identity')==identity and saved.get('volume_bytes')==volume_bytes and saved.get('policy_version')==POLICY_VERSION and saved.get('name')==name:
            outputs=[]
            for item in saved['outputs']:
                filename=item['name']
                if Path(filename).name!=filename or filename in ('.','..'):raise ExtractionError('Invalid repack receipt.')
                path=work/filename
                if path.is_symlink() or not path.is_file() or path.stat().st_size!=item['size'] or digest(path)!=item['sha256']:break
                outputs.append(path)
            else:
                if outputs:return outputs
    if stopped():raise ExtractionError('Stopped before repackaging; source retained.')
    with tempfile.TemporaryDirectory(prefix='repack-',dir=work) as temporary:
        temporary=Path(temporary);members=temporary/'members';members.mkdir();output=temporary/'packed';output.mkdir()
        all_entries={};release_total=0;prefixes=set();removed=[];pdfs=[]
        for spec in sources:
            prefix=spec['folder']
            if prefix and any(part in ('','.','..') or safe_component(part)!=part for part in prefix.split('/')):raise ExtractionError('Unsafe source folder.')
            if prefix.casefold() in prefixes:raise ExtractionError('Source folders would collide.')
            prefixes.add(prefix.casefold());target_members=members/prefix;target_members.mkdir(parents=True,exist_ok=True)
            with split_input_aliases(Path(spec['path']).resolve(strict=True),temporary) as (read_source,_):
                progress('unpacking',0)
                _,entries=listing(read_source,temporary,stopped)
                if spec.get('include_prefixes') is not None:
                    allowed=spec['include_prefixes']
                    if any(not any(e['name'].startswith(prefix.rstrip('/')+'/') for e in entries) for prefix in allowed):
                        raise ExtractionError('A selected source is missing from the downloaded repack; preparation stopped.')
                    entries=[e for e in entries if any(e['name'].startswith(prefix.rstrip('/')+'/') for prefix in allowed)]
                removed.extend({'source':str(spec['path']),'name':e['name']} for e in entries if is_metadata_file(e['name']))
                entries=[e for e in entries if not is_metadata_file(e['name'])]
                if not entries:continue
                total=sum(e['size'] for e in entries);release_total+=total
                if len(all_entries)+len(entries)>100000 or release_total>500*1024**3:raise ExtractionError('Release exceeds the local repackaging limit.')
                if len(entries)>100000 or total>500*1024**3:raise ExtractionError('Release exceeds the local repackaging limit; source retained.')
                if shutil.disk_usage(work).free<total*2+release_total-total+RESERVE:raise ExtractionError('Not enough local space to unpack and repack the release; source retained.')
                if not entries:raise ExtractionError('Archive contains no files to repackage.')
                mapped={}
                for entry in entries:
                    key=entry.get('disk_name',entry['name']).casefold()
                    if key in mapped:raise ExtractionError('Repackaged filenames would collide.')
                    mapped[key]=entry
                ordinary=[e for e in entries if 'disk_name' not in e]
                include=temporary/'include.txt';include.write_text(''.join(e['name']+'\n' for e in ordinary))
                if ordinary:
                    command(['x','-y','-bsp1','-bb0','-mmt=2','-p-','-spd','-scsUTF-8','-o'+str(target_members),'-i@'+str(include),'--',str(read_source)],temporary,stopped,percent=lambda n:progress('unpacking',n),timeout=14400)
                for entry in entries:
                    if 'disk_name' in entry:extract_mapped_member(read_source,entry,member_path(target_members,entry),temporary,stopped,lambda:None)
                for entry in entries:
                    path=member_path(target_members,entry)
                    if path.is_symlink() or not path.is_file() or path.stat().st_size!=entry['size']:raise ExtractionError('Extracted content does not match the source archive.')
                actual={p.relative_to(target_members).as_posix() for p in target_members.rglob('*') if p.is_file()}
                expected={e.get('disk_name',e['name']) for e in entries}
                if actual!=expected or any(p.is_symlink() for p in target_members.rglob('*')):raise ExtractionError('Unexpected content in unpacked release.')
            all_entries.update({(prefix+'/'+e.get('disk_name',e['name'])).lstrip('/'):e['size'] for e in entries})
        if not all_entries:raise ExtractionError('No release files remain after removing operating-system metadata; source retained.')
        from mmf_pdf import clean_pdf
        for member_name in list(all_entries):
            if Path(member_name).suffix.lower()!='.pdf':continue
            if stopped():raise ExtractionError('Stopped before PDF processing; source retained.')
            progress('processing_pdfs',0)
            path=members/member_name
            before={'size':path.stat().st_size,'sha256':digest(path)}
            try:result=clean_pdf(path,stopped)
            except Exception as error:raise ExtractionError('PDF processing failed for '+member_name+': '+str(error)) from error
            all_entries[member_name]=path.stat().st_size
            pdfs.append({'name':member_name,'before':before,'after':{'size':path.stat().st_size,'sha256':digest(path)},**result})
        progress('repacking',0)
        command(['a','-t7z','-mx='+str(COMPRESSION_LEVEL),'-mmt=2','-ms=off','-bsp1','-bb0','-v'+str(volume_bytes)+'b','--',str(output/name),'.'],temporary,stopped,percent=lambda n:progress('repacking',n),timeout=14400,cwd=members)
        parts=sorted(output.iterdir())
        if not parts or any(p.stat().st_size>volume_bytes for p in parts):raise ExtractionError('Repacked volume size exceeds the configured limit.')
        if len(parts)==1 and parts[0].name==name+'.001':parts[0].rename(output/name);parts=[output/name]
        progress('verifying_archive',0)
        command(['t','-bsp1','-bb0','-mmt=2','-p-','--',str(parts[0])],temporary,stopped,percent=lambda n:progress('verifying_archive',n),timeout=14400)
        _,packed_entries=listing(parts[0],temporary,stopped)
        if {e['name']:e['size'] for e in packed_entries}!=all_entries:raise ExtractionError('Repacked contents differ from the extracted release.')
        outputs=[];records=[]
        for path in parts:
            checksum=digest(path);target=work/path.name;path.replace(target);outputs.append(target);records.append({'name':target.name,'size':target.stat().st_size,'sha256':checksum})
        state={'pdfs':pdfs,'removed_metadata':removed,'name':name,'compression_level':COMPRESSION_LEVEL,'policy_version':POLICY_VERSION,'volume_bytes':volume_bytes,'identity':identity,'outputs':records}
        staged=work/'repack.json.tmp';staged.write_text(json.dumps(state));staged.replace(receipt)
        return outputs
