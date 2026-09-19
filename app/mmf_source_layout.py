"""Readable original-archive storage, preserving names and multipart sets."""
from pathlib import Path
import re
from app.file_delivery import digest
from app.release_images import safe_component, volume_key


def source_units(items):
    groups={}
    for item in items:
        groups.setdefault((item['object_id'],volume_key(item['filename'])[0]),[]).append(item)
    result=[]
    for group in groups.values():
        if any(volume_key(i['filename'])[1] for i in group):
            names=[safe_component(i['filename']).casefold() for i in group]
            if len(names)!=len(set(names)):
                raise ValueError('Multiple multipart archive versions use the same part names. Their matching parts need review.')
            result.append(sorted(group,key=lambda i:volume_key(i['filename'])[1]))
        else:
            # A creator may upload distinct archives with exactly the same name.
            result.extend([item] for item in group)
    return result


def source_directory(root, unit, paths, reserved=None):
    """Use the root unless names collide; resolve only collisions in readable folders.

    reserved also supports planning a migration before moving any files.
    Existing equal files are reusable only after checking their full contents.
    """
    root=Path(root);reserved=reserved if reserved is not None else {}
    head=unit[0]
    model=re.sub(r'[\\/<>:"|?*\x00-\x1f]', '-',head.get('object_name') or Path(head['filename']).stem)[:120].strip(' .') or 'Model'
    if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])',model):model='Model '+model
    archive_id=head.get('archive_id') or head.get('object_id') or 'archive'
    candidates=[root,root/model,root/f'{model} (MMF {archive_id})']
    def same(a,b):
        return a==b or (a.is_file() and not a.is_symlink() and b.is_file() and not b.is_symlink()
                       and a.stat().st_size==b.stat().st_size and digest(a)==digest(b))
    for number in range(1,1001):
        directory=candidates[number-1] if number<=3 else root/f'{model} (MMF {archive_id}, version {number-2})'
        if directory.is_symlink() or directory.exists() and not directory.is_dir():continue
        existing={p.name.casefold():p for p in directory.iterdir()} if directory.is_dir() else {}
        selected=[]
        for item in unit:
            source=Path(paths[item['key']]);target=directory/source.name
            key=str(target).casefold();other=reserved.get(key) or existing.get(source.name.casefold())
            if other and (Path(other).name!=source.name or not same(Path(other),source)):break
            selected.append((key,source))
        else:
            reserved.update(selected)
            return directory
    raise ValueError('Too many conflicting versions of this original archive.')


def migrate_legacy_sources(manager, apply=False):
    """Move legacy source sets on the same filesystem, with a resumable journal.

    Run explicitly while the application's workers are idle. Only generated
    originals/<16 hex digits> directories are flattened. No release archives,
    images or unrelated folders are removed.
    """
    import json
    import os
    import re
    import sqlite3
    import time
    from app.file_delivery import mount_identity, rename_no_replace
    base=Path(manager.store.config()['download_directory']).resolve(strict=True)
    recovery=manager.directory/'source-layout-migration'
    journal=recovery/'journal.json'
    def rewrite(value,mapping):
        if isinstance(value,str):return mapping.get(value,value)
        if isinstance(value,list):return [rewrite(v,mapping) for v in value]
        if isinstance(value,dict):return {mapping.get(k,k):rewrite(v,mapping) for k,v in value.items()}
        return value
    with manager.idle():
        from app.mmf_release_upload import busy
        if busy(manager):raise FileExistsError('Wait for the release upload to finish.')
        with manager.db() as db:
            tables=[(name,key,column) for name,key,column in [('completed','key','data'),('state','key','value'),('telegram_preparations','id','data'),('release_uploads','id','data')]
                    if db.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?',(name,)).fetchone()]
            contents={name:list(db.execute(f'SELECT {key},{column} FROM {name}')) for name,key,column in tables}
        if journal.exists():
            plan=json.loads(journal.read_text())
            if plan.get('base')!=str(base):raise ValueError('Migration belongs to a different download directory.')
            if plan.get('complete'):return plan
        else:
            lookup={};roots=set()
            for _,raw in contents['completed']:
                item=json.loads(raw)
                for output in (item.get('repack') or {}).get('outputs',[]):
                    path=Path(output['path'])
                    if 'MMF sources' not in path.parts:continue
                    root=Path(*path.parts[:path.parts.index('MMF sources')+1])
                    if not root.is_relative_to(base):continue
                    roots.add(root);lookup[str(path)]=item
            moves=[];directories=[];reserved={}
            for root in sorted(roots):
                old=root/'originals'
                if not old.is_dir():continue
                if any(p.is_symlink() for p in (old,*old.parents)):raise ValueError('Symbolic link in source layout.')
                for directory in sorted(old.iterdir()):
                    if not re.fullmatch(r'[0-9a-f]{16}',directory.name) or not directory.is_dir():continue
                    files=sorted(directory.iterdir())
                    if any(p.is_symlink() or not p.is_file() for p in files):raise ValueError('Unexpected content in '+str(directory))
                    directories.append(str(directory))
                    if not files:continue
                    unit=[];paths={}
                    for path in files:
                        item={**lookup.get(str(path),{}),'key':str(path),'filename':path.name}
                        unit.append(item);paths[item['key']]=path
                    target=source_directory(root,unit,paths,reserved)
                    moves.extend({'from':str(p),'to':str(target/p.name),'size':p.stat().st_size} for p in files)
                directories.append(str(old))
            plan={'base':str(base),'created_at':time.time(),'moves':moves,'directories':directories,'complete':False}
        if not apply:return plan
        recovery.mkdir(parents=True,exist_ok=True)
        if not journal.exists():
            with manager.db() as db,sqlite3.connect(recovery/'manager-before.sqlite3') as backup:db.backup(backup)
            manager.store.atomic_write(journal,plan)
        identity=mount_identity(base)
        for move in plan['moves']:
            source=Path(move['from']);target=Path(move['to'])
            for p in (source,target):
                if not p.is_relative_to(base) or any(a.is_symlink() for a in (p,*p.parents)):raise ValueError('Unsafe migration path.')
            if mount_identity(base)!=identity:raise ValueError('Destination mount changed; migration paused.')
            if source.exists():
                if source.stat().st_size!=move['size']:raise ValueError('Source changed during migration: '+source.name)
                target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():
                    if target.stat().st_size!=move['size'] or digest(source)!=digest(target):raise ValueError('Conflicting destination; source retained: '+str(target))
                    # A verified identical copy remains at the new destination.
                    source.unlink()
                else:
                    sourcefd=os.open(source.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                    targetfd=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                    try:rename_no_replace(sourcefd,source.name,targetfd,target.name)
                    finally:os.close(sourcefd);os.close(targetfd)
            if not target.is_file() or target.stat().st_size!=move['size']:raise ValueError('Migrated file is missing or changed: '+str(target))
        mapping={m['from']:m['to'] for m in plan['moves']}
        with manager.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for name,key,column in tables:
                for identity,raw in contents[name]:
                    changed=json.dumps(rewrite(json.loads(raw),mapping))
                    if json.loads(raw)!=json.loads(changed):db.execute(f'UPDATE {name} SET {column}=? WHERE {key}=?',(changed,identity))
        removed=0
        for directory in sorted(set(plan['directories']),key=lambda p:len(Path(p).parts),reverse=True):
            try:Path(directory).rmdir();removed+=1
            except FileNotFoundError:pass
            except OSError:
                if not list(Path(directory).iterdir()):raise
        plan.update(complete=True,removed_directories=removed,completed_at=time.time())
        manager.store.atomic_write(journal,plan)
        return plan
