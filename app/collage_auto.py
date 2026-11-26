"""Deterministic, local collage suggestions. No model inference or network access."""
import base64
import collections
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import subprocess
import sys
import uuid
import zipfile
from PIL import Image,ImageOps,ImageStat,ImageFilter

VARIANTS={'supported','unsupported','presupported','pre','parts','part','single','lychee','renders','render','stl','zip','rar','7z','scale','copy'}
GENERIC={'untitled','main','camera','full','quality','fullquality','image','img','photo','render','renders','preview','layout','jpg','png'}
MONTHS={'january','february','march','april','may','june','july','august','september','october','november','december'}

def words(value):
    value=re.sub(r'([a-z0-9])([A-Z])',r'\1 \2',value)
    return re.findall(r'[a-z]+|\d+',value.casefold())

def model_name(value):
    tokens=[w for w in words(Path(value).stem) if w not in VARIANTS and not re.fullmatch(r'r\d+',w)]
    return ' '.join(tokens)

def broad_archive(name):
    from app.release_rules import release_month
    tokens=words(Path(name).stem)
    return (bool(release_month(name)) or bool(set(tokens)&(MONTHS|{'welcome','collection','release','rewards','images','campaign','stretchgoals','bundle'}))
            or bool(tokens and tokens[0] in ('render','renders','images')))

def origins(store,release,records):
    """Recover old receipt provenance as well as newly stored member paths."""
    destination=Path(release['base'])/release['folder']/release['month']/'release_images'
    by_name=collections.defaultdict(list);models=set();zip_cache={};products={}
    for provider in ('mmf','gumroad'):
        database=store.root/'data'/provider/'manager.sqlite3'
        if not database.exists():continue
        with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
            rows=db.execute('SELECT data FROM completed').fetchall()
            prepared=db.execute("SELECT value FROM state WHERE key LIKE 'prepared_images:%'").fetchall()
        for raw, in rows:
            item=json.loads(raw);manifest=item.get('repack') or {}
            if item.get('folder')!=release['folder'] or item.get('release_folder')!=release['month']:continue
            archive=item.get('filename','');model=model_name(archive)
            products[str(item.get('object_id',''))]={'archive':item.get('object_name',''),'model':model_name(item.get('object_name','')),'broad':broad_archive(item.get('object_name','')),'member':''}
            if model and not broad_archive(archive):models.add(model)
            images=manifest.get('images',[])
            for image in images:
                try:relative=str(Path(image['path']).relative_to(destination))
                except (KeyError,ValueError):continue
                member=image.get('member_path','')
                # Legacy flat images retain basenames. Read ZIP directories only,
                # never unpack an archive just to suggest a collage.
                if not member and broad_archive(archive):
                    for output in manifest.get('outputs',[]):
                        source=Path(output['path'])
                        if source.suffix.lower()!='.zip' or not source.is_relative_to(destination.parent):continue
                        if '..' in source.parts or not source.resolve().is_relative_to(destination.parent.resolve()):continue
                        if any(p.is_symlink() for p in (source,*source.parents)):continue
                        if str(source) not in zip_cache:
                            try:
                                with zipfile.ZipFile(source) as z:
                                    names=z.namelist();zip_cache[str(source)]=names if len(names)<100000 else []
                            except (OSError,zipfile.BadZipFile):zip_cache[str(source)]=[]
                        basename=relative.split('--')[-1]
                        matches=[n for n in zip_cache[str(source)] if Path(n).name==basename]
                        if len(matches)==1:member=matches[0];break
                by_name[relative].append({'archive':archive,'model':model,'broad':broad_archive(archive),'member':member,'object_id':str(item.get('object_id',''))})
        for raw, in prepared:
            for image in json.loads(raw).get('images',[]):
                try:relative=str(Path(image['path']).relative_to(destination))
                except (KeyError,ValueError):continue
                if image.get('original_name'):
                    by_name[relative].append({**products.get(str(image.get('object_id')),{'model':'','broad':True}),'original_name':image['original_name'],'member':image.get('member_path','')})
    with store.store.history.connect() as db:
        for row in db.execute('SELECT filename,images_destination,images_manifest FROM downloads WHERE images_manifest IS NOT NULL'):
            try:
                prefix=Path(row['images_destination']).relative_to(destination)
                for image in json.loads(row['images_manifest']):
                    name=str(prefix/image['path'])
                    by_name[name].append({'archive':row['filename'],'model':model_name(row['filename']),'broad':broad_archive(row['filename']),'member':image.get('source_path','')})
            except (TypeError,ValueError,KeyError):continue
    # Telegram extraction manifests can retain their archive association.
    for record in records:
        if by_name.get(record['relative']):continue
        match=re.match(r'(?:gallery--)?(\d+)-',record['name'])
        if match and match[1] in products:
            by_name[record['relative']].append(products[match[1]]);continue
        archive=record.get('archive','')
        if archive and archive not in ('Other images',release['month']):
            model=model_name(archive)
            by_name[record['relative']].append({'archive':archive,'model':model,'broad':broad_archive(archive),'member':record['relative']})
            if not broad_archive(archive):models.add(model)
    return by_name,models

def grouping(record,source,models):
    filename=next((s['original_name'] for s in source if s.get('original_name')),record['name'].split('--')[-1]);tokens=[w for w in words(Path(filename).stem) if w not in VARIANTS]
    candidates=[]
    for model in models:
        if set(model.split())<=set(tokens):
            candidates.append(((len(model.split()),int(''.join(tokens).startswith(model.replace(' ','')))),model))
    candidates.sort(reverse=True)
    specific={s['model'] for s in source if not s['broad'] and s['model']}
    if len(specific)==1:return next(iter(specific)),3,'Model archive',False
    if candidates:
        ambiguous=len(candidates)>1 and candidates[0][0][0]==candidates[1][0][0]
        return candidates[0][1],2,'Filename matches model archives',ambiguous
    for s in source:
        parent=Path(s.get('member','')).parent.name
        if parent not in ('','.'): 
            tokens_parent=set(words(parent))
            if tokens_parent-GENERIC-VARIANTS and model_name(parent)!=s['model']:
                return s['model']+' / '+parent,2,'Folder inside collection archive',True
    meaningful=[t for t in tokens if t not in GENERIC and not t.isdigit() and t not in ('v','r','gallery')]
    if re.match(r'(?:gallery--)?\d+-[a-f0-9]{16}',record['name']):meaningful=[]
    if meaningful:return ' '.join(meaningful),1,'Filename clue',True
    if len(source)==1 and source[0].get('model'):
        return source[0]['model'],1,'Collection archive; individual models unclear',True
    return 'unknown:'+record['id'],0,'Random fallback; model unknown',True

def features(data):
    with Image.open(io.BytesIO(data)) as im:
        im=ImageOps.exif_transpose(im).convert('RGB');width,height=im.size
        im.thumbnail((128,128));pixels=list(im.getdata())
        color=sum(max(p)-min(p)>25 for p in pixels)/max(1,len(pixels))
        small=im.resize((64,64));raw=small.tobytes()
        gray=list(im.convert('L').resize((9,8)).getdata())
        dhash=sum((gray[y*9+x]>gray[y*9+x+1])<<(y*8+x) for y in range(8) for x in range(8))
        sharpness=ImageStat.Stat(im.convert('L').filter(ImageFilter.FIND_EDGES)).var[0]
    return {'version':2,'width':width,'height':height,'color':color,'dhash':dhash,'pixels':base64.b64encode(raw).decode(),'sharpness':sharpness}

def near_duplicate(a,b):
    if abs(a['width']/a['height']-b['width']/b['height'])>.025:return False
    if (a['dhash']^b['dhash']).bit_count()>1:return False
    differences=[abs(x-y) for x,y in zip(a['_pixels'],b['_pixels'])]
    return max(differences)<=8 and sum(differences)/len(differences)<.75

def choose(candidates,seed=0):
    rng=random.Random(seed);ordered=sorted(candidates,key=lambda i:i['id'])
    for item in ordered:
        name=set(words(item.get('ranking_name',item['name'].split('--')[-1])))
        group_shot=bool(name&{'combo','group','overview','all','collection','cover'})
        diagram=bool(name&{'scale','recipe','instructions','instruction','layout','heads','components','supports'})
        item['group_shot_hint']=group_shot;item['diagram_hint']=diagram
        item['score']=math.log2(max(1,item['width']*item['height']))+min(item['color']*15,10)+min(item['sharpness']/1000,3)+(20 if group_shot else 0)-(35 if diagram else 0)
        item['_tie']=rng.random();item['_pixels']=base64.b64decode(item['pixels'])
    # Prefer quality before deduplication, so the retained copy is representative.
    ordered.sort(key=lambda i:(-i['score'],i['_tie'] if seed else i['id']))
    unique=[];duplicates=[];hashes=set()
    for item in ordered:
        if item['sha256'] in hashes or any(near_duplicate(item,other) for other in unique):duplicates.append(item['id']);continue
        hashes.add(item['sha256']);unique.append(item)
    groups=collections.defaultdict(list)
    for item in unique:groups[item['group']].append(item)
    suitable=[i for i in unique if not i['diagram_hint'] and min(i['width'],i['height'])>=256]
    primary=[];fallback=[]
    for group,items in groups.items():
        good=[i for i in items if i in suitable] or ([] if suitable else items)
        if not good:fallback.extend(items);continue
        winner=good[0]
        if seed:
            alternatives=[i for i in good if i['score']>=winner['score']-3 and i['group_shot_hint']==winner['group_shot_hint']]
            winner=rng.choice(alternatives)
        (primary if winner['confidence']>=2 else fallback).append(winner)
    primary.sort(key=lambda i:(-i['score'],i['_tie']))
    selected=primary[:9];counts=collections.Counter(i['group'] for i in selected)
    # Include unresolved groups before giving known groups a second image.
    rng.shuffle(fallback)
    for item in fallback:
        if len(selected)>=9:break
        if not counts[item['group']]:selected.append(item);counts[item['group']]+=1
    extras=[i for i in unique if i not in selected and not i['diagram_hint']]
    extras.sort(key=lambda i:(-i['score'],i['_tie']))
    for item in extras:
        if len(selected)>=9:break
        if counts[item['group']]<2 and all((item['dhash']^s['dhash']).bit_count()>=10 for s in selected if s['group']==item['group']):
            selected.append(item);counts[item['group']]+=1
    cards=[];singles=[]
    for group,items in sorted(groups.items()):
        useful=[i for i in items if not i['diagram_hint'] and min(i['width'],i['height'])>=256] or items[:1]
        name=re.sub(r'\bV (\d+)\b',r'V\1',group.title()) if not group.startswith('unknown:') else 'Unidentified model'
        if len(useful)<2:singles.append((useful[0],name));continue
        useful=sorted(useful,key=lambda i:(not i['group_shot_hint'],-i['score'],i['_tie']))[:9]
        cards.append({'id':hashlib.sha256(('set:'+group).encode()).hexdigest()[:32],'name':name[:80],'kind':'model','enabled':True,'layout':'featured',
                      'images':[i['id'] for i in useful],'labels':{},'uncertain':any(i['uncertain'] for i in useful)})
    for offset in range(0,len(singles),9):
        chunk=singles[offset:offset+9];name='Release Overview'+(' '+str(offset//9+1) if len(singles)>9 else '')
        cards.append({'id':hashlib.sha256(('overview:'+str(offset//9)).encode()).hexdigest()[:32],'name':name,'kind':'overview','enabled':True,'layout':'rows',
                      'images':[i['id'] for i,n in chunk],'labels':{i['id']:n[:80] for i,n in chunk},'uncertain':any(i['uncertain'] for i,n in chunk)})
    card_ids={i for card in cards for i in card['images']}
    card_selected=[{'id':i['id'],'name':i['name'],'group':i['group'],'reason':i['reason'],'uncertain':i['uncertain'] or i['diagram_hint'],'quality':('Group-shot filename hint; ' if i['group_shot_hint'] else '')+('diagram fallback' if i['diagram_hint'] else 'resolution and color preference')} for i in unique if i['id'] in card_ids]
    return {'cards':cards,'card_selected':card_selected,'images':[i['id'] for i in selected],'selected':[{'id':i['id'],'name':i['name'],'group':i['group'],'reason':i['reason'],'uncertain':i['uncertain'] or i['diagram_hint'],
            'quality':('Group-shot filename hint; ' if i['group_shot_hint'] else '')+('diagram fallback' if i['diagram_hint'] else 'resolution and color preference')} for i in selected],
            'duplicates_removed':len(duplicates),'groups_found':len(groups),'candidates':len(candidates),'uncertain':sum(i['uncertain'] or i['diagram_hint'] for i in selected)}

def start(collages,payload):
    release=collages.release(payload.get('release_id'));ids=payload.get('image_ids');seed=payload.get('seed',0)
    if not isinstance(ids,list) or not 1<=len(ids)<=5000 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):raise ValueError('Choose a release with 1–5000 distinct images.')
    if type(seed) is not int or not 0<=seed<=2**31-1:raise ValueError('Invalid shuffle seed.')
    records=[collages.image_record(i) for i in ids]
    if any(r['release_id']!=release['id'] for r in records):raise ValueError('All images must belong to the selected release.')
    with (collages.cache/'auto-worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise FileExistsError('Auto-fill is already analyzing images. Please wait.')
        job={'id':uuid.uuid4().hex,'release_id':release['id'],'image_ids':ids,'seed':seed,'state':'running','done':0,'total':len(ids),'message':'Reading image sources…'}
        collages.store.atomic_write(collages.cache/('auto-'+job['id']+'.json'),job)
        with (collages.cache/'auto-worker.log').open('a') as log:
            try:subprocess.Popen([sys.executable,'-m','app.collage_auto',str(collages.root),job['id'],str(lock.fileno())],cwd=collages.root,pass_fds=(lock.fileno(),),stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
            except Exception:
                job.update(state='failed',message='Could not start Auto-fill.');collages.store.atomic_write(collages.cache/('auto-'+job['id']+'.json'),job);raise
    return job

def status(collages,job_id):
    if not re.fullmatch('[a-f0-9]{32}',str(job_id)):raise ValueError('Invalid Auto-fill job.')
    job=json.loads((collages.cache/('auto-'+job_id+'.json')).read_text())
    if job['state']=='running':
        with (collages.cache/'auto-worker.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:pass
            else:job.update(state='failed',message='Auto-fill was interrupted. Try again.')
    return job

def execute(collages,job):
    def save():collages.store.atomic_write(collages.cache/('auto-'+job['id']+'.json'),job)
    try:
        release=collages.release(job['release_id']);records=[collages.image_record(i) for i in job['image_ids']]
        if any(r['release_id']!=release['id'] for r in records):raise ValueError('Release selection changed.')
        origin,models=origins(collages,release,records);candidates=[];errors=[]
        with collages.db() as db:db.execute('CREATE TABLE IF NOT EXISTS auto_features (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        for number,record in enumerate(records,1):
            job.update(message='Analyzing '+record['name'],done=number-1);save()
            try:
                # Always verify current identity, even when feature data is cached.
                with collages.pinned(release) as pinned:
                    if pinned.info(release['month']+'/release_images/'+record['relative'])!=json.loads(record['identity']):raise ValueError('Image changed; reopen the release.')
                with collages.db() as db:cached=db.execute('SELECT data FROM auto_features WHERE id=?',(record['id'],)).fetchone()
                feature=json.loads(cached[0]) if cached else None
                if not feature or feature.get('version')!=2:
                    data,checksum=collages.source(record);feature={**features(data),'sha256':checksum}
                    with collages.db() as db:db.execute('INSERT OR REPLACE INTO auto_features VALUES (?,?)',(record['id'],json.dumps(feature)))
                group,confidence,reason,uncertain=grouping(record,origin[record['relative']],models)
                ranking_name=next((s['original_name'] for s in origin[record['relative']] if s.get('original_name')),record['name'].split('--')[-1])
                candidates.append({**record,**feature,'ranking_name':ranking_name,'group':group,'confidence':confidence,'reason':reason,'uncertain':uncertain})
            except (OSError,ValueError) as error:errors.append(record['name']+': '+str(error))
            job['done']=number;save()
        if not candidates:raise ValueError('No readable images remain. Reopen the release and try again.')
        job.update(message='Grouping and selecting images…');save()
        result=choose(candidates,job['seed'])
        job.update(state='complete',message='Suggestion ready. Review the grid before saving.',result=result,warnings=errors);save()
    except Exception as error:job.update(state='failed',message=str(error));save()

if __name__=='__main__':
    from app.collages import CollageStore
    from app.subscription_store import SubscriptionStore
    store=CollageStore(SubscriptionStore(Path(sys.argv[1])))
    try:execute(store,json.loads((store.cache/('auto-'+sys.argv[2]+'.json')).read_text()))
    finally:os.close(int(sys.argv[3]))
