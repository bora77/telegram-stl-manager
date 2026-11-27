'use strict';
const el=id=>document.getElementById(id);
const activeExport=job=>['queued','rendering','saving'].includes(job.state);
let folders=[],context=null,pickerVersion=0,opening=false,draggedImage=null;
const pendingDrafts=new Set();
const make=(tag,text='',className='')=>{const n=document.createElement(tag);n.textContent=text;if(className)n.className=className;return n};
const signature=draft=>JSON.stringify(draft);
function error(message=''){el('page-error').textContent=message;el('page-error').hidden=!message}
async function api(path,data){const r=await fetch(path,{cache:'no-store',...(data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})});const b=await r.json();if(!r.ok)throw Error(b.error||'Request failed.');return b}
function cardKind(card){return card.kind||card.draft?.card_kind||(Object.keys(card.draft?.labels||{}).length>1||card.name.toLowerCase().includes('overview')?'overview':'model')}
function payload(c,draft=c.draft){const card=draft.cards?.find(i=>i.id===draft.active_card);return {...draft,...(card?{card_name:card.name,card_kind:cardKind(card)}:{}),release_id:c.release.id,revision:c.revision}}
function cardDraft(draft){return {images:[...draft.images],labels:{...(draft.labels||{})},layout:draft.layout,shape:draft.shape,theme:draft.theme,title:draft.title}}
function baseTitle(c){const artist=c.release.folder.replace(/^[-! ]+/,'').trim(),month=c.release.month;return artist+' · '+(month.toLowerCase().startsWith(artist.toLowerCase()+' ')?month.slice(artist.length).trim():month)}
function cardTitle(c,name){const suffix=' · '+name,base=baseTitle(c);return (base.length+suffix.length<=120?base:base.slice(0,Math.max(1,119-suffix.length))+'…')+suffix}
function syncCard(c){const card=c.draft.cards?.find(i=>i.id===c.draft.active_card);if(card)card.draft=cardDraft(c.draft)}
function loadDraftControls(c){for(const key of ['layout','shape','theme'])el(key).value=c.draft[key];el('slot-count').value=c.draft.images.length;el('collage-title').value=c.draft.title;renderCards(c)}
function selectCard(id){const c=context;if(!c)return;syncCard(c);const cards=c.draft.cards,card=cards.find(i=>i.id===id);if(!card)return;c.draft={...structuredClone(card.draft),cards,active_card:id};c.active=0;loadDraftControls(c);changed()}
function renderCards(c){
 const cards=c.draft.cards||[];el('collage-cards').replaceChildren();el('collage-card-tools').hidden=!cards.length;
 for(const card of cards){const box=make('div','','collage-card'+(card.id===c.draft.active_card?' active':'')),button=make('button','','collage-card-select');button.type='button';button.setAttribute('aria-pressed',String(card.id===c.draft.active_card));const first=card.draft.images.find(Boolean);if(first){const img=make('img');img.src='/api/collages/thumbnail/'+first;img.alt='';button.append(img)}button.append(make('strong',card.name),make('small',`${card.draft.images.filter(Boolean).length} images${card.uncertain?' · Review suggestion':''}`));if(card.feature_basis)button.append(make('small',card.feature_basis));button.onclick=()=>selectCard(card.id);const label=make('label','Include in upload '),check=make('input');check.type='checkbox';check.checked=card.enabled;check.onchange=()=>{card.enabled=check.checked;changed()};label.append(check);box.append(button,label);el('collage-cards').append(box)}
 const card=cards.find(i=>i.id===c.draft.active_card);el('collage-card-name').value=card?.name||'';
 el('collage-card-kind').value=card?cardKind(card):'model';
 el('save-collage').textContent=cards.length?'Save selected collages':'Save collage';
}
function listWarnings(id,messages){el(id).replaceChildren(...messages.map(s=>make('li',s)));el(id).hidden=!messages.length}
function clearWorkspace(){context=null;el('workspace').hidden=true;el('empty-state').hidden=false;error()}
async function autoFill(shuffle=false){
 const c=context;if(!c||c.autoBusy||c.exportPending)return;
 const archive=el('archive-filter').value,ids=c.images.filter(i=>!i.reason&&(!archive||i.archive===archive)).map(i=>i.id);
 if(!ids.length){error('No usable images match the selected archive.');return}
 const before=structuredClone(c.draft),beforeSignature=signature(c.draft);c.autoBusy=true;c.autoSeed=shuffle?(c.autoSeed||0)+1:0;
 el('auto-fill').disabled=el('auto-shuffle').disabled=true;el('auto-status').textContent='Starting local analysis…';el('auto-progress').hidden=false;el('auto-progress').removeAttribute('value');error();
 try{
  let job=await api('/api/collages/auto-fill',{release_id:c.release.id,image_ids:ids,seed:c.autoSeed});
  while(job.state==='running'){
   if(context!==c)return;
   el('auto-status').textContent=`${job.message} · ${job.done}/${job.total}`;el('auto-progress').max=job.total||1;el('auto-progress').value=job.done||0;
   await new Promise(resolve=>setTimeout(resolve,500));job=await api('/api/collages/auto-fill?id='+encodeURIComponent(job.id));
  }
  if(context!==c)return;
  if(job.state!=='complete')throw Error(job.message||'Auto-fill failed.');
  if(signature(c.draft)!==beforeSignature||el('archive-filter').value!==archive)throw Error('The grid or archive filter changed during analysis. Your edits were kept; click Auto-fill again to apply a fresh suggestion.');
  c.autoUndo=before;const cards=(job.result.cards||[]).map(card=>({...card,draft:{images:card.images,labels:card.labels,layout:card.layout,shape:'square',theme:before.theme,title:cardTitle(c,card.name)}}));
  if(cards.length){c.draft={...structuredClone(cards[0].draft),cards,active_card:cards[0].id};}else c.draft.images=job.result.images;
  c.active=0;loadDraftControls(c);el('auto-undo').disabled=false;
  el('auto-status').textContent=`${cards.length||1} collage(s) proposed · ${job.result.duplicates_removed} duplicates removed. Multi-image sets get their own collage; single-image models share overview collages. Review before saving.`;
  el('auto-reasons').replaceChildren(...(job.result.card_selected||job.result.selected).map(i=>{const row=make('li',`${i.name} — ${i.group.replace(/^unknown:.*/, 'Unknown model')} · ${i.reason} · ${i.quality}${i.uncertain?' · Review':''}`);row.dataset.uncertain=String(i.uncertain);return row;}));
  for(const warning of job.warnings||[])el('auto-reasons').append(make('li',warning));
  el('auto-details').hidden=false;changed();
 }catch(e){if(context===c){error(e.message);el('auto-status').textContent='Auto-fill did not change your grid.';}}
 finally{c.autoBusy=false;if(context===c){el('auto-fill').disabled=el('auto-shuffle').disabled=false;el('auto-progress').hidden=true;}}
}
function filterFolders(){
 const old=el('folder').value,q=el('folder-search').value.trim().toLowerCase(),matches=folders.filter(f=>f.toLowerCase().includes(q));
 el('folder').replaceChildren(new Option(q&&!matches.length?'No matching folders':'Choose an artist folder',''),...matches.map(f=>new Option(f,f)));
 el('folder').value=matches.includes(old)?old:q&&matches.length?matches[0]:'';
 if(el('folder').value!==old)loadMonths();
}
async function loadMonths(preferred=null){
 if(typeof preferred!=='string')preferred=null;
 const version=++pickerVersion,folder=el('folder').value;clearWorkspace();el('month').replaceChildren(new Option(preferred||'Choose a month',preferred||''));el('month').disabled=true;el('open-release').disabled=true;
 let logo=document.getElementById('collage-artist-logo');if(!logo){logo=document.createElement('span');logo.id='collage-artist-logo';el('folder').parentElement.append(logo)}
 logo.replaceChildren(...(folder?[ArtistProfiles.badge(folder.replace(/^[-!\s]+/,''))]:[]));
 el('picker-status').textContent=folder?'Finding releases with extracted images…':'Choose an artist folder.';
 if(!folder)return;
 try{const data=await api('/api/collages/months?folder='+encodeURIComponent(folder));if(version!==pickerVersion)return;
  el('month').replaceChildren(...(data.months.length?data.months.map(m=>new Option(m,m)):[new Option('No extracted images','')]));el('month').disabled=!data.months.length;el('open-release').disabled=!data.months.length;
  if(preferred){el('month').value=data.months.includes(preferred)?preferred:'';el('open-release').disabled=!el('month').value;if(!el('month').value)throw Error('The selected release has no extracted images: '+preferred)}
  el('picker-status').textContent=data.months.length?`${data.months.length} releases with images.`:'Extract images for this artist first, using Downloads or Organize folders.';
 }catch(e){if(version===pickerVersion){error(e.message);el('picker-status').textContent='Could not read release folders.'}}
}
function showImage(record){el('image-dialog-title').textContent=record.name;el('large-image').src='/api/collages/image/'+record.id;el('image-dialog').showModal()}
function renderGallery(){
 const c=context;if(!c)return;
 const q=el('image-search').value.trim().toLowerCase(),archive=el('archive-filter').value;
 const matches=c.images.filter(i=>i.relative.toLowerCase().includes(q)&&(!archive||i.archive===archive));
 el('gallery').replaceChildren();el('gallery-empty').hidden=!!matches.length;el('image-count').textContent=`(${c.images.filter(i=>!i.reason).length})`;
 for(const record of matches){
  const card=make('article','','image-card'+(record.reason?' unavailable':''));card.dataset.imageId=record.id;card.draggable=!record.reason;
  if(!record.reason){const img=make('img');img.src='/api/collages/thumbnail/'+record.id;img.alt=record.name;img.loading='lazy';img.draggable=false;card.append(img)}
  const name=make('span',record.name,'name');name.title=record.relative;card.append(name,make('small',record.reason||`${record.width} × ${record.height}`,'card-meta'));
  if(!record.reason){
   const actions=make('div','','card-actions'),place=make('button','Place'),view=make('button','View');place.className='place-image';place.onclick=()=>placeImage(record.id,c.active);view.onclick=()=>showImage(record);actions.append(place,view);card.append(actions);
   card.addEventListener('dragstart',event=>beginDrag(event,record.id));card.addEventListener('dragend',endDrag);
  }
  el('gallery').append(card);
 }
 updateGallerySelection();
}
function updateGallerySelection(){
 if(!context)return;
 for(const card of el('gallery').children){const slot=context.draft.images.indexOf(card.dataset.imageId);card.classList.toggle('used',slot>=0);const button=card.querySelector('.place-image');if(button){button.textContent=slot>=0?`Slot ${slot+1}`:'Place';button.title='Place this image in selected slot '+(context.active+1)}}
}
function beginDrag(event,id){draggedImage=id;event.dataTransfer.effectAllowed='copyMove';event.dataTransfer.setData('application/x-stl-image',id);event.dataTransfer.setData('text/plain',id)}
function endDrag(){draggedImage=null;document.querySelectorAll('.drag-over').forEach(n=>n.classList.remove('drag-over'))}
function placeImage(id,target){
 const c=context;if(!c||!c.byId.has(id)||c.byId.get(id).reason||target<0||target>=c.draft.images.length)return;
 const from=c.draft.images.indexOf(id);if(from===target){selectSlot(target);return}
 if(from>=0)c.draft.images[from]=c.draft.images[target];c.draft.images[target]=id;c.active=target;changed();
}
function selectSlot(index){if(!context)return;context.active=index;renderSlots();updateGallerySelection()}
function renderSlots(){
 const c=context;if(!c||!c.geometry)return;
 const grid=el('collage-grid'),[width,height]=c.geometry.size;grid.style.aspectRatio=`${width} / ${height}`;grid.classList.toggle('light',c.draft.theme==='light');
 grid.querySelectorAll('.slot').forEach(n=>n.remove());el('canvas-title').textContent=c.draft.title;
 const margin=Math.min(width,height)/30;Object.assign(el('canvas-title').style,{left:margin/width*100+'%',right:margin/width*100+'%',top:margin*.8/height*100+'%',fontSize:margin/width*100+'cqw'});
 for(const {index,rect} of c.geometry.cells){
  if(index>=c.draft.images.length)continue;
  const id=c.draft.images[index],record=id?c.byId.get(id):null,slot=make('button','','slot'+(record?' filled':'')+(index===c.active?' active':''));
  slot.dataset.slot=String(index);slot.setAttribute('aria-label',`Slot ${index+1}: ${record?record.name:'empty, drop an image here'}`);slot.setAttribute('aria-pressed',String(index===c.active));slot.draggable=!!record;
  Object.assign(slot.style,{left:rect[0]/width*100+'%',top:rect[1]/height*100+'%',width:(rect[2]-rect[0])/width*100+'%',height:(rect[3]-rect[1])/height*100+'%'});
  if(record){const img=make('img');img.src='/api/collages/thumbnail/'+id;img.alt='';img.draggable=false;slot.append(img)}else slot.append(make('span','Drop image','empty-hint'));
  if(record&&c.draft.labels?.[id]){slot.classList.add('has-caption');slot.append(make('span',c.draft.labels[id],'slot-caption'));}
  slot.append(make('span',index===0&&c.draft.layout==='featured'?'Featured':String(index+1),'slot-number'));slot.onclick=()=>selectSlot(index);
  slot.ondragstart=event=>{if(record)beginDrag(event,id);else event.preventDefault()};slot.ondragend=endDrag;
  slot.ondragover=event=>{if(draggedImage||event.dataTransfer.types.includes('application/x-stl-image')){event.preventDefault();event.dataTransfer.dropEffect='move';slot.classList.add('drag-over')}};
  slot.ondragleave=()=>slot.classList.remove('drag-over');slot.ondrop=event=>{event.preventDefault();const value=event.dataTransfer.getData('application/x-stl-image')||draggedImage;endDrag();if(value)placeImage(value,index)};
  grid.append(slot);
 }
 const filled=c.draft.images.filter(Boolean).length;el('selected-count').textContent=`${filled} / ${c.draft.images.length} images`;el('slot-status').textContent=`Selected slot ${c.active+1}`;
 el('remove-image').disabled=!c.draft.images[c.active];el('move-left').disabled=!c.draft.images[c.active]||c.active===0;el('move-right').disabled=!c.draft.images[c.active]||c.active===c.draft.images.length-1;
 el('image-label').disabled=!c.draft.images[c.active];el('image-label').value=c.draft.labels?.[c.draft.images[c.active]]||'';
}
function queueDraft(c){
 if(c.saving)return c.saving;
 c.saving=(async()=>{
  while(signature(c.draft)!==c.savedSignature){
   const draft=structuredClone(c.draft),sig=signature(draft);if(context===c)el('draft-status').textContent='Saving draft…';
   const response=await api('/api/collages/selection',payload(c,draft));c.revision=response.revision;c.savedSignature=sig;c.saveError=null;
  }
  if(context===c)el('draft-status').textContent='Draft saved · original images stay unchanged.';
 })().catch(e=>{c.saveError=e.message;if(context===c){error(e.message);el('draft-status').textContent='Draft could not be saved.'}}).finally(()=>{pendingDrafts.delete(c.saving);c.saving=null});
 pendingDrafts.add(c.saving);
 return c.saving;
}
async function updateLayout(c){
 const seq=++c.layoutSeq,data=payload(c);
 try{const geometry=await api('/api/collages/layout',data);if(context!==c||seq!==c.layoutSeq)return;c.geometry=geometry;renderSlots()}
 catch(e){if(context===c&&seq===c.layoutSeq)error(e.message)}
}
function changed(){
 const c=context;if(!c)return;syncCard(c);renderCards(c);c.preview=null;el('save-collage').disabled=true;el('preview-details').hidden=true;listWarnings('quality-warnings',[]);renderSlots();updateGallerySelection();queueDraft(c);updateLayout(c);
 el('preview-status').textContent=c.draft.images.every(Boolean)?'Updating preview…':'Fill every slot to preview and save your collage.';
 clearTimeout(c.previewTimer);c.previewTimer=setTimeout(()=>preview(c),550);
}
async function preview(c){
 if(context!==c||!c.draft.images.every(Boolean))return;
 if(c.previewing){c.previewAgain=true;return}
 c.previewing=true;const draft=structuredClone(c.draft),sig=signature(draft);
 try{const result=await api('/api/collages/preview',payload(c,draft));if(context!==c||signature(c.draft)!==sig)return;
  c.preview={...result,signature:sig};el('preview-image').src=result.url;el('preview-details').hidden=false;el('preview-status').textContent=`Preview ready · ${result.output_size.join(' × ')} px · full images, no cropping.`;listWarnings('quality-warnings',result.warnings);renderExports();
 }catch(e){if(context===c&&signature(c.draft)===sig){error(e.message);el('preview-status').textContent='Preview could not be created. Reopen the images to refresh them.'}}
 finally{c.previewing=false;if(c.previewAgain){c.previewAgain=false;preview(c)}}
}
function renderExports(){
 const c=context;if(!c)return;
 const job=c.exports.find(activeExport),cards=c.draft.cards?.filter(i=>i.enabled);el('export-progress').hidden=!job;el('save-collage').disabled=!!job||c.exportPending||c.autoBusy||(cards?(!cards.length||cards.some(i=>!i.draft.images.every(id=>id&&c.byId.has(id)))):(!c.preview||c.preview.signature!==signature(c.draft)));el('auto-fill').disabled=el('auto-shuffle').disabled=!!(c.exportPending||c.autoBusy);
 if(job){el('export-message').textContent=job.message;if(job.total>0)el('export-bar').value=Math.min(100,100*(job.done||0)/job.total);else el('export-bar').removeAttribute('value');el('export-detail').textContent=job.total?`${((job.done||0)/1e6).toFixed(1)} / ${(job.total/1e6).toFixed(1)} MB`+(job.speed>0?` · ${(job.speed/1e6).toFixed(1)} MB/s`:''):'Preparing full-resolution images…'}
 el('no-exports').hidden=!!c.exports.length;el('versions').replaceChildren();
 for(const entry of c.exports){const li=make('li'),text=make('div','','version-text');text.append(make('span',entry.filename),make('small',entry.state==='completed'?new Date(entry.finished*1000).toLocaleString()+(entry.backup_available?' · Local backup':' · Saved'):entry.message));li.append(text);
  if(entry.state==='completed'){const link=make('a','Download JPEG');link.href='/api/collages/result/'+entry.id;link.download=entry.filename;li.append(link)}
  else if(['failed','interrupted'].includes(entry.state)){const retry=make('button','Retry save');retry.disabled=!!job||c.exportPending;retry.onclick=()=>saveCollage(entry.id);li.append(retry)}
  el('versions').append(li);
 }
}
async function pollExports(c){
 if(context!==c||!c.exports.some(activeExport))return;
 try{
  for(const job of c.exports.filter(activeExport)){const result=await api('/api/collages/export?id='+job.id);c.exports[c.exports.findIndex(e=>e.id===job.id)]=result}
  if(!c.exports.some(activeExport)){
   const updated=await Promise.all(c.exports.map(job=>api('/api/collages/export?id='+job.id)));
   for(const job of updated){const index=c.exports.findIndex(e=>e.id===job.id);if(index>=0)c.exports[index]=job}
  }
  if(context===c)renderExports();
 }
 catch(e){if(context===c)error(e.message)}
 if(context===c&&c.exports.some(activeExport))setTimeout(()=>pollExports(c),1000);
}
async function saveCollage(retry){
 if(!retry&&context?.draft.cards)return saveCollageSet();
 const c=context;if(!c||c.exportPending||c.autoBusy)return;const selected=c.preview;
 if(!retry&&(!selected||selected.signature!==signature(c.draft)))return;
 c.exportPending=true;renderExports();error();
 try{const job=await api('/api/collages/export',retry?{retry_id:retry}:{preview_id:selected.id});c.exports=[job,...c.exports.filter(j=>j.id!==job.id)];if(context===c){renderExports();pollExports(c)}}
 catch(e){if(context===c)error(e.message)}finally{c.exportPending=false;if(context===c)renderExports()}
}
async function saveCollageSet(){
 const c=context;if(!c||c.exportPending||c.autoBusy)return;syncCard(c);const snapshot=signature(c.draft),cards=structuredClone(c.draft.cards.filter(i=>i.enabled));if(!cards.length)return;
 c.exportPending=true;renderExports();error();
 try{
  await queueDraft(c);if(c.saveError)throw Error(c.saveError);
  const previews=[],names=new Set();
  for(const [index,card] of cards.entries()){
   if(context!==c)throw Error('Release changed; remaining collage saves stopped.');
   el('auto-status').textContent=`Preparing collage ${index+1}/${cards.length}: ${card.name}`;
   const result=await api('/api/collages/preview',{...card.draft,release_id:c.release.id,card_name:card.name,card_kind:cardKind(card)});
   if(names.has(result.filename.toLowerCase()))throw Error('Collage output names collide. Give each card a distinct name.');names.add(result.filename.toLowerCase());previews.push(result);
  }
  const ids=[];
  for(const [index,preview] of previews.entries()){
   if(context!==c)throw Error('Release changed; remaining collage saves stopped.');
   el('auto-status').textContent=`Saving collage ${index+1}/${cards.length}: ${cards[index].name}`;
   let job=await api('/api/collages/export',{preview_id:preview.id});c.exports=[job,...c.exports.filter(i=>i.id!==job.id)];
   while(activeExport(job)){if(context===c)renderExports();await new Promise(resolve=>setTimeout(resolve,500));job=await api('/api/collages/export?id='+job.id);c.exports[c.exports.findIndex(i=>i.id===job.id)]=job;}
   if(job.state!=='completed')throw Error(job.message||'Collage save failed.');ids.push(job.id);
  }
  await api('/api/collages/set',{release_id:c.release.id,export_ids:ids});
  if(context===c)el('auto-status').textContent=`${ids.length} collages saved and selected for release upload. Archives will follow them.`+(signature(c.draft)!==snapshot?' Newer edits have not been saved.':'');
 }catch(e){if(context===c)error(e.message)}finally{c.exportPending=false;if(context===c)renderExports()}
}
el('folder-search').oninput=filterFolders;el('folder').onchange=loadMonths;el('month').onchange=()=>{clearWorkspace();el('open-release').disabled=!el('month').value};
el('open-release').onclick=async()=>{
 if(opening)return;opening=true;error();el('open-release').disabled=true;const folder=el('folder').value,month=el('month').value,version=pickerVersion;
 el('picker-status').textContent='Reading image dimensions and saved draft…';
 try{await Promise.allSettled([...pendingDrafts]);const result=await api('/api/collages/open',{folder,month});if(version!==pickerVersion||folder!==el('folder').value||month!==el('month').value)return;
  const c={...result,draft:structuredClone(result.selection),byId:new Map(result.images.map(i=>[i.id,i])),active:0,layoutSeq:0,preview:null,exports:result.exports||[]};
  c.savedSignature=signature(result.selection);c.draft.images=c.draft.images.map(id=>id&&c.byId.has(id)&&!c.byId.get(id).reason?id:null);context=c;
  renderCards(c);
  el('auto-status').textContent='';el('auto-details').hidden=true;el('auto-progress').hidden=true;el('auto-fill').disabled=el('auto-shuffle').disabled=false;el('auto-undo').disabled=true;
  for(const key of ['layout','shape','theme'])el(key).value=c.draft[key];el('slot-count').value=c.draft.images.length;el('collage-title').value=c.draft.title;
  el('image-search').value='';el('archive-filter').replaceChildren(new Option('All archives',''),...[...new Set(c.images.map(i=>i.archive))].sort().map(a=>new Option(a,a)));
  const source=new URLSearchParams(location.search),requestedArchive=source.get('folder')===folder&&source.get('month')===month?source.get('archive'):null;
  const selectedArchive=c.images.some(image=>image.archive===requestedArchive)?requestedArchive:null;
  const autoRequested=source.get('autofill')==='1'&&source.get('folder')===folder&&source.get('month')===month;
  if(selectedArchive)el('archive-filter').value=selectedArchive;
  el('collage-grid').querySelectorAll('.slot').forEach(n=>n.remove());el('canvas-title').textContent='';
  const artist=folder.replace(/^[-! ]+/,'').trim()||folder,date=month.match(/(?:^|[^0-9])(20[0-9]{2}-(?:0[1-9]|1[0-2]))(?:$|[^0-9])/);
  const label=date?date[1]:month.toLowerCase().startsWith(artist.toLowerCase()+' ')?month.slice(artist.length).replace(/^[ _-]+/,''):month;
  const filename=artist+'-'+label+'.jpg';
  history.replaceState(null,'','/collages?'+new URLSearchParams({folder,month,...(selectedArchive?{archive:selectedArchive}:{})}));
  el('workspace').hidden=false;el('empty-state').hidden=true;el('preview-details').hidden=true;el('destination').textContent=`${folder}/${month}/${filename} · Replaces the previous collage, keeping a local backup`;el('picker-status').textContent=`${folder} · ${month} · ${c.images.length} images`;
  listWarnings('scan-warnings',result.warnings);renderGallery();renderExports();changed();pollExports(c);
  if(autoRequested){if(!c.draft.images.some(Boolean))await autoFill(false);else el('auto-status').textContent='Existing draft kept. Use Auto-fill to replace its selection.';}
 }catch(e){error(e.message);el('picker-status').textContent='Could not open this release.'}
 finally{opening=false;el('open-release').disabled=!el('month').value}
};
for(const id of ['layout','shape','theme'])el(id).onchange=()=>{if(context){context.draft[id]=el(id).value;changed()}};
el('auto-fill').onclick=()=>autoFill(false);el('auto-shuffle').onclick=()=>autoFill(true);
el('auto-undo').onclick=()=>{if(!context?.autoUndo)return;context.draft=context.autoUndo;context.autoUndo=null;context.active=0;loadDraftControls(context);el('auto-undo').disabled=true;el('auto-status').textContent='Previous grid restored.';el('auto-details').hidden=true;changed();};
el('collage-card-name').oninput=()=>{const c=context,card=c?.draft.cards?.find(i=>i.id===c.draft.active_card);if(!card)return;const old=card.name;card.name=el('collage-card-name').value;if(c.draft.title===cardTitle(c,old)){c.draft.title=cardTitle(c,card.name);el('collage-title').value=c.draft.title}changed()};
el('new-collage-card').onclick=()=>{const c=context;if(!c)return;syncCard(c);const id=()=>crypto.randomUUID().replaceAll('-','');if(!c.draft.cards)c.draft.cards=[{id:id(),name:'Release Overview',enabled:true,draft:cardDraft(c.draft)}];const name='New collage '+(c.draft.cards.length+1),card={id:id(),name,enabled:true,draft:{images:[null],labels:{},layout:'featured',shape:'square',theme:c.draft.theme,title:cardTitle(c,name)}};c.draft.cards.push(card);selectCard(card.id)};
el('remove-collage-card').onclick=()=>{const c=context;if(!c?.draft.cards||c.draft.cards.length<2)return;c.draft.cards=c.draft.cards.filter(i=>i.id!==c.draft.active_card);selectCard(c.draft.cards[0].id)};
el('image-label').oninput=()=>{const c=context,id=c?.draft.images[c.active];if(!id)return;c.draft.labels||={};c.draft.labels[id]=el('image-label').value;changed()};
el('collage-card-kind').onchange=()=>{const c=context,card=c?.draft.cards?.find(i=>i.id===c.draft.active_card);if(card){card.kind=el('collage-card-kind').value;changed()}};
el('slot-count').onchange=()=>{if(context){const n=Number(el('slot-count').value);context.draft.images=Array.from({length:n},(_,i)=>context.draft.images[i]||null);context.active=Math.min(context.active,n-1);changed()}};
el('collage-title').oninput=()=>{if(context){context.draft.title=el('collage-title').value;changed()}};
el('image-search').oninput=renderGallery;el('archive-filter').onchange=()=>{const url=new URL(location.href);const archive=el('archive-filter').value;if(archive)url.searchParams.set('archive',archive);else url.searchParams.delete('archive');history.replaceState(null,'',url);renderGallery()};
el('remove-image').onclick=()=>{if(context){context.draft.images[context.active]=null;changed()}};
for(const [id,offset] of [['move-left',-1],['move-right',1]])el(id).onclick=()=>{if(context)placeImage(context.draft.images[context.active],context.active+offset)};
el('save-collage').onclick=()=>saveCollage();el('close-image').onclick=()=>el('image-dialog').close();
window.addEventListener('beforeunload',event=>{if(pendingDrafts.size||(context&&signature(context.draft)!==context.savedSignature)){event.preventDefault();event.returnValue=''}});
(async()=>{try{const config=await api('/api/config');folders=config.folders||[];filterFolders();const target=new URLSearchParams(location.search);if(target.has('folder')){const folder=target.get('folder'),month=target.get('month');if(!folders.includes(folder))throw Error('The requested artist folder is unavailable.');el('folder').value=folder;el('folder-search').value=folder.replace(/^[-! ]+/,'');el('picker-status').textContent='Opening '+month+'…';await loadMonths(month);if(![...el('month').options].some(o=>o.value===month))throw Error('The requested release has no extracted images.');el('month').value=month;await el('open-release').onclick();return}el('picker-status').textContent=folders.length?'Choose an artist folder.':'No folders available. Check the download folder in Configuration.'}catch(e){error(e.message);el('picker-status').textContent='Could not load folders.'}})();
