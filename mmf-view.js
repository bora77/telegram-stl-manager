(()=>{
const $=id=>document.getElementById(id);let data=null,draft=[],dirty=false,page=0,inFlight=false;
let artistPage=0;
const expandedReleases=new Set();let queueSignature='';
function showMMFView(){
 const artists=location.hash==='#artists';
 $('availability').hidden=artists;$('subscriptions').hidden=!artists;$('mmf-download-controls').hidden=artists;$('mmf-releases').hidden=artists;
 for(const [id,active] of [['mmf-artists-tab',artists],['mmf-tab',!artists]]){const link=$(id);if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current')}
 document.title='Telegram STL manager · MMF '+(artists?'Artists':'Queue');
}
window.addEventListener('hashchange',showMMFView);showMMFView();

const text=(tag,value)=>{const n=document.createElement(tag);n.textContent=value;return n};
async function request(action,payload){const r=await fetch('/api/mmf'+(action?'/'+action:''),{cache:'no-store',...(payload!==undefined?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{})});const value=await r.json();if(!r.ok)throw Error(value.error||'Request failed');return value}
function notice(value,error=false){$('notice').textContent=value;$('notice').className=error?'error':''}
function changed(){
 const canonical=rows=>JSON.stringify([...rows].sort((a,b)=>a.id-b.id).map(s=>[s.id,s.folder,s.start_month]));
 dirty=canonical(draft)!==canonical(data.settings.subscriptions);$('save').disabled=!dirty||data.active;
 $('selected-count').textContent=`${draft.length} creators selected`;
 $('sub-count').textContent=`${data.settings.subscriptions.length} subscriptions saved · manual downloads only`;
 const status=$('artist-save-status');status.hidden=!dirty;status.textContent=dirty?'Unsaved changes. Press Save subscriptions to apply them.':'';
}
function artists(){const body=$('artists');body.replaceChildren();const search=$('search').value.toLowerCase(),mode=$('subscription-filter').value;
 const saved=new Set(data.settings.subscriptions.map(s=>s.id));
 const matches=data.creators.filter(c=>c.name.toLowerCase().includes(search)&&(!mode||(mode==='subscribed'?saved.has(c.id):!saved.has(c.id)))).sort((a,b)=>a.name.localeCompare(b.name));
 artistPage=Math.min(artistPage,Math.max(0,Math.ceil(matches.length/50)-1));
 $('artist-count').textContent=`${matches.length} matching creators · subscription filter uses saved subscriptions`;
 $('artists-page').textContent=matches.length?`${artistPage*50+1}–${Math.min((artistPage+1)*50,matches.length)}`:'No matches';
 $('artists-previous').disabled=artistPage===0;$('artists-next').disabled=(artistPage+1)*50>=matches.length;
 changed();
 for(const creator of matches.slice(artistPage*50,artistPage*50+50)){let sub=draft.find(s=>s.id===creator.id),defaults=sub||data.settings.subscriptions.find(s=>s.id===creator.id);const row=document.createElement('tr'),toggle=document.createElement('input');toggle.type='checkbox';toggle.className='toggle';toggle.checked=!!sub;toggle.setAttribute('role','switch');toggle.setAttribute('aria-label','Subscribe to '+creator.name);toggle.disabled=data.active;
 const folder=document.createElement('input');folder.setAttribute('list','folders');folder.setAttribute('aria-label',creator.name+' folder');folder.value=defaults?.folder||data.folders.find(f=>f.replace(/^[-\s]+/,'').toLowerCase()===creator.name.toLowerCase())||creator.name;
 const scope=document.createElement('select');scope.setAttribute('aria-label',creator.name+' scope');scope.append(new Option('From month/year','month'),new Option('All (archiving)','all'));scope.value=defaults&&defaults.start_month===''?'all':'month';const month=document.createElement('input');month.type='month';month.setAttribute('aria-label',creator.name+' starting month');const now=new Date();now.setDate(1);now.setMonth(now.getMonth()-1);month.value=defaults?.start_month||`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}`;
 const enable=()=>{folder.disabled=scope.disabled=month.disabled=!toggle.checked||data.active;month.hidden=scope.value==='all'};enable();
 const membership=text('small','');membership.className='changed';const updateMembership=()=>{membership.textContent=toggle.checked!==saved.has(creator.id)?(saved.has(creator.id)?'Removal pending':'Not saved yet'):''};updateMembership();
 const update=()=>{if(toggle.checked){const old=draft.findIndex(s=>s.id===creator.id);const entry={id:creator.id,name:creator.name,folder:folder.value,start_month:scope.value==='all'?'':month.value};if(old<0)draft.push(entry);else draft[old]=entry}else draft=draft.filter(s=>s.id!==creator.id);enable();changed();updateMembership()};toggle.onchange=folder.oninput=scope.onchange=month.oninput=update;
 const identity=text('span',creator.name);identity.prepend(ArtistProfiles.badge(creator.name));
 for(const node of [toggle,identity,folder]){const td=document.createElement('td');td.append(node);if(node===toggle)td.append(membership);row.append(td)}const td=document.createElement('td');const scopeRow=document.createElement('div');scopeRow.className='artist-scope-row';scopeRow.append(scope,month);td.append(scopeRow);row.append(td);const infoCell=document.createElement('td');infoCell.className='artist-info-cell';infoCell.append(ArtistProfiles.infoButton(creator.name,[{label:'Source',text:'MyMiniFactory · Shared with me',href:'https://www.myminifactory.com/library#/shared-with-me'},{label:'MMF creator ID',text:String(creator.id)}]));row.append(infoCell);body.append(row);
 }}
function releaseGroups(){
 const groups=new Map();
 for(const item of data.items){const key=item.release_key||JSON.stringify([item.creator_id,item.release_month?['month',item.release_month]:item.release_id?['id',String(item.release_id)]:item.release?['label',item.release]:['object',item.object_id]]);let group=groups.get(key);if(!group){group={key,creator:item.creator,name:item.release_display_name||item.release||item.object_name,items:[]};groups.set(key,group)}group.items.push(item)}
 return [...groups.values()].map(g=>({...g,size:g.items.reduce((sum,i)=>sum+i.size,0),recorded:g.items.filter(i=>i.completed).length,review:g.items.filter(i=>!i.completed&&!i.month).length,months:[...new Set(g.items.map(i=>i.release_month).filter(Boolean))].sort()})).sort((a,b)=>a.creator.localeCompare(b.creator)||(b.months.at(-1)||'').localeCompare(a.months.at(-1)||'')||a.name.localeCompare(b.name));
}
function sizeLabel(bytes){return bytes>=1e9?(bytes/1e9).toFixed(2)+' GB':(bytes/1e6).toFixed(1)+' MB'}
const closedUploadPanels=new Set();
function uploadPanel(pack){
 const job=pack.upload;if(!job)return null;
 const panel=document.createElement('details');panel.className='upload-panel';panel.open=!closedUploadPanels.has(job.id);
 panel.ontoggle=()=>{if(!panel.isConnected)return;if(panel.open)closedUploadPanels.delete(job.id);else closedUploadPanels.add(job.id)};
 const live=['queued','preparing','uploading','verifying'].includes(job.state);
 const heading=document.createElement('summary'),icon=text('span',live?'':job.state==='complete'?'✓':'!');if(live)icon.className='upload-spinner';heading.append(icon,text('strong','Release upload · '+(job.state||'unknown')));panel.append(heading);
 const body=document.createElement('div');body.className='upload-body';body.append(text('p',job.message||''));
 if(live){const metrics=document.createElement('div');metrics.className='upload-metrics';const bar=document.createElement('progress');bar.max=job.total||1;if(job.total)bar.value=job.bytes||0;bar.setAttribute('aria-label','Total upload progress');metrics.append(bar,text('strong',(job.speed_mbps||0).toFixed(1)+' MB/s'));body.append(metrics,text('small',sizeLabel(job.bytes||0)+' / '+sizeLabel(job.total||0)+' uploaded'));}
 for(const f of job.files||[]){const row=document.createElement('div');row.className='upload-file';row.append(text('span',f.name||''),text('small',sizeLabel(f.size||0)+' · '+f.state));if(['uploading','verifying'].includes(f.state)){const bar=document.createElement('progress');bar.max=f.size||1;bar.value=f.uploaded||0;bar.setAttribute('aria-label',(f.name||'File')+' upload progress');row.append(bar)}body.append(row)}
 panel.append(body);return panel;
}
function workflowStep(button,number,state,label){
 const step=document.createElement('div');step.className='workflow-step';step.dataset.state=state;
 const heading=document.createElement('div');heading.className='workflow-heading';
 const marker=text('span',state==='done'?'✓':state==='blocked'?'—':String(number));marker.className='workflow-marker';marker.setAttribute('aria-hidden','true');
 heading.append(marker,text('strong',state==='done'?'Done':state==='running'?'In progress':state==='blocked'?'Blocked':number===1?button.textContent:'Ready'));
 step.title=label;step.append(heading,button);return step;
}
function releaseStatus(group){
 const lifecycle=data.release_lifecycle?.[group.key],prep=data.preparations?.[group.key],collage=data.collages?.[group.key];
 if(group.items.some(i=>i.storage_unavailable))return 'Storage unavailable';
 if(!data.active&&group.items.some(i=>i.missing_files?.length))return 'Files missing · Re-download release';
 if(lifecycle?.finished)return 'Released';
 const percent=(done,total)=>total>0?' · '+Math.min(100,Math.floor(100*(done||0)/total))+'%':'';
 const packages=prep?.packages||[],uploading=packages.find(p=>['queued','preparing','uploading','verifying'].includes(p.upload?.state));
 if(uploading)return (uploading.upload.state==='verifying'?'Verifying upload':'Uploading')+percent(uploading.upload.bytes,uploading.upload.total);
 if(data.active&&data.job?.release_key===group.key){
  const job=data.job,phase={starting:'Starting',downloading:'Downloading',unpacking:'Unpacking archives',processing_pdfs:'Cleaning PDFs',repacking:'Compressing archive',verifying_archive:'Verifying archive',verifying_inputs:'Verifying files',extracting:'Extracting images',moving:'Moving files'}[job.phase]||(job.action==='prepare_images'?'Preparing images':job.action==='package'?'Making release':'Processing release');
  return phase+percent(job.bytes,job.expected)+(job.image_total!=null?` · ${job.image_count||0}/${job.image_total} images`:'');
 }
 if(group.recorded<group.items.length)return `${group.recorded}/${group.items.length} downloaded`;
 const issued=new Set(prep?.keys||[]),fresh=group.items.some(i=>i.completed&&!issued.has(i.key));
 const made=prep?.number>=0&&!fresh&&!prep.pending;
 if(made){
  const pending=packages.filter(p=>!p.published);
  if(pending.length&&pending.every(p=>p.upload?.state==='complete'))return 'Awaiting publication';
  if(pending.some(p=>['failed','error','stopped','interrupted'].includes(p.upload?.state)))return 'Upload incomplete · Retry upload';
  return 'Archive ready · Upload release';
 }
 if(!collage?.ready)return group.items.every(i=>i.images_checked)?'No images · Download from MMF':'Extract images';
 if(!collage?.valid)return 'Images ready · Create collage';
 return prep?.pending?'Archive incomplete · Retry packaging':lifecycle?.published?'Collage ready · Make addendum':'Collage ready · Make release';
}
function queue(){
 const groups=releaseGroups(),rows=groups.filter(g=>$('filter').value==='all'||($('filter').value==='done'?data.release_lifecycle?.[g.key]?.finished:!data.release_lifecycle?.[g.key]?.finished));
 page=Math.min(page,Math.max(0,Math.ceil(rows.length/50)-1));
 const signature=JSON.stringify([data.items,data.preparations,data.collages,data.release_lifecycle,data.active,data.job?.action,data.job?.release_key,data.job?.phase,data.job?.bytes,data.job?.expected,data.job?.image_count,data.job?.image_total,$('filter').value,page]);if(signature===queueSignature)return;queueSignature=signature;$('queue').replaceChildren();
 for(const group of rows.slice(page*50,page*50+50)){
  const details=document.createElement('details');details.className='release-row'+(group.items.some(i=>i.missing_files?.length)?' missing-files':'');details.open=expandedReleases.has(group.key);details.ontoggle=()=>{if(!details.isConnected)return;if(details.open)expandedReleases.add(group.key);else expandedReleases.delete(group.key)};
  const summary=document.createElement('summary'),heading=document.createElement('span');heading.className='release-heading';const creatorLabel=text('small',group.creator);creatorLabel.prepend(ArtistProfiles.badge(group.creator,{compact:true}));heading.append(text('strong',group.name),creatorLabel);
  const info=document.createElement('span');info.className='release-info';info.append(text('span',`${group.items.length} ${group.items.length===1?'file':'files'} · ${sizeLabel(group.size)}`));
  const first=group.items[0];info.append(text('small',(first.release_folder||group.name)+(first.release_month_basis==='created_at'?' · from MMF creation date':'')));
  const lifecycle=data.release_lifecycle?.[group.key];const status=text('span',releaseStatus(group));status.className='release-status '+(group.items.some(i=>i.missing_files?.length)?'missing':lifecycle?.finished?'recorded':'');summary.append(heading,info,status);details.append(summary);
  if(first.release_month||data.collages?.[group.key]){
   const prep=data.preparations?.[group.key],issued=new Set(prep?.keys||[]),fresh=group.items.filter(i=>i.completed&&!issued.has(i.key));
   const actions=document.createElement('div');actions.className='actions';actions.style.padding='12px 18px';
   const offerGallery=group.recorded===group.items.length&&group.items.every(i=>i.images_checked)&&!data.collages?.[group.key]?.ready;
   const prepare=text('button',offerGallery?'Download images from MMF':'Extract images');prepare.disabled=data.active||!group.recorded;prepare.title=offerGallery?'Download gallery images from MMF for this release.':'Extract release images without creating an archive.';prepare.onclick=()=>act(offerGallery?'images':'prepare',{release_key:group.key});
   const button=text('button',prep?.pending?'Retry '+prep.pending:prep&&prep.number>=0?`Make Addendum ${prep.number+1}`:'Make release');
   button.disabled=!data.collages?.[group.key]?.valid||data.active||group.recorded!==group.items.length||(!fresh.length&&!prep?.pending);
   button.onclick=()=>act('package',{release_key:group.key});
   const collage=data.collages?.[group.key];
   if(!collage?.valid&&first.release_month){button.title='Create and save a valid collage before making this release.';}
   const collageButton=text('button',collage?.exists?'Edit collage':'Create collage');collageButton.className='secondary';collageButton.disabled=!collage?.ready;
   collageButton.title=collage?.ready?'Open this release in the collage editor.':'The release_images folder must contain images. Download and extract release images first.';
   collageButton.onclick=()=>{location.href='/collages?'+new URLSearchParams({folder:collage.folder,month:collage.release,archive:collage.release})};
   const jobHere=data.active&&data.job?.release_key===group.key;
   const preparing=jobHere&&data.job?.action==='prepare_images',packaging=jobHere&&data.job?.action==='package';
   const made=prep?.number>=0&&!fresh.length&&!prep.pending&&group.recorded===group.items.length;
   const workflow=document.createElement('div');workflow.className='release-workflow';workflow.setAttribute('role','group');workflow.setAttribute('aria-label','Release workflow');
   const steps=[workflowStep(prepare,1,preparing?'running':collage?.ready?'done':prepare.disabled?'blocked':'ready',preparing?'Extracting release images…':collage?.ready?'Release images available':!group.recorded?'Download release files first':data.active?'Wait for the current MMF task':'Extract images for the collage'),
    workflowStep(collageButton,2,collage?.valid?'done':collageButton.disabled?'blocked':'ready',collage?.valid?'Saved collage available':!collage?.ready?'Prepare release images first':'Choose images and save the collage')];
   if(first.release_month){
    if(made)button.textContent='Make release';
    steps.push(workflowStep(button,3,packaging?'running':made?'done':button.disabled?'blocked':'ready',packaging?'Building the archive…':made?'Release archive ready':group.recorded!==group.items.length?'Download remaining files first':!collage?.valid?'Save a valid collage first':data.active?'Wait for the current MMF task':'Build the release archive'));
   }
   steps.forEach((step,index)=>{if(index){const arrow=text('span','→');arrow.className='workflow-arrow';arrow.setAttribute('aria-hidden','true');workflow.append(arrow)}workflow.append(step)});actions.append(workflow);
   if(prep?.directory)actions.append(text('small','Prepared: '+prep.title+' · '+prep.directory));
   else if(first.release_month)actions.append(text('small','Creates a 7-Zip set using your configured compression and part size. Later additions get numbered addenda.'));
   for(const pack of prep?.packages||[]){
    const upload=text('button',pack.attempted?'Re-upload':'Upload release');
    upload.disabled=prep.upload_active;upload.title=pack.title;
    upload.onclick=()=>{expandedReleases.add(group.key);act('upload',{preparation_id:pack.id})};
    actions.append(upload);
    if(pack.published)actions.append(text('small','✓ Released · '+pack.title));
    else if(pack.upload?.state==='complete'){
     const finish=text('button','Confirm released');finish.className='secondary';finish.title='Record completion after the release bot confirms publication.';
     finish.onclick=()=>{if(window.confirm('Has the release bot successfully finished publishing '+pack.title+'? Uploading to Release Pad or seeing the Complete button is not enough.'))act('released',{preparation_id:pack.id,attempt_id:pack.upload.id,confirmed:true})};actions.append(finish);
    }
   }
   details.append(actions);
   for(const pack of prep?.packages||[]){const progress=uploadPanel(pack);if(progress)details.append(progress);}
  }
  const box=document.createElement('div');box.className='release-files table';const table=document.createElement('table'),head=document.createElement('thead'),header=document.createElement('tr');for(const label of ['File','Size','Status'])header.append(text('th',label));head.append(header);table.append(head);const body=document.createElement('tbody');
  for(const item of group.items){
   const row=document.createElement('tr'),name=document.createElement('td');if(item.missing_files?.length)row.className='missing-file';name.append(text('span',item.filename));if(item.object_name&&item.object_name!==group.name)name.append(text('small',item.object_name));row.append(name,text('td',sizeLabel(item.size)));row.append(text('td',item.missing_files?.length?'Archive missing':item.completed?'Downloaded':'Ready'));body.append(row)
  }
  table.append(body);box.append(table);details.append(box);
  const retryActions=document.createElement('div');retryActions.className='actions';retryActions.style.padding='12px 18px';
  const retry=text('button','Re-download release');retry.className='secondary';retry.disabled=data.active||!data.connected;retry.title='Download fresh copies of this release. Existing history, images and collage are retained.';
  retry.onclick=()=>{expandedReleases.add(group.key);act('redownload',{release_key:group.key})};retryActions.append(retry);details.append(retryActions);
$('queue').append(details)
 }
 $('page').textContent=rows.length?`${page+1} / ${Math.ceil(rows.length/50)} · ${rows.length} releases`:'No releases to show';$('previous').disabled=page===0;$('next').disabled=(page+1)*50>=rows.length;
}
function render(reset=false){$('connection').hidden=data.connected;$('sub-count').textContent=`${data.settings.subscriptions.length} subscribed · ${data.creators.length} artists`;
 $('folders').replaceChildren(...data.folders.map(f=>new Option(f,f)));if(reset||!draft.length&&!dirty){draft=structuredClone(data.settings.subscriptions);artists()}
 for(const id of ['check','download','resume'])$(id).disabled=data.active||!data.connected||dirty;
 $('resume').disabled ||= !data.resumable;
 $('download').disabled ||= !data.counts.available||!data.checked_at;$('stop').disabled=!data.active;$('save').disabled=!dirty||data.active;changed();
 const next=data.next_check_at?new Date(data.next_check_at*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'due now';
 const checking=data.active&&data.job.action==='check';
 const pending=data.availability_claimed?[]:data.items.filter(i=>!i.completed);
 const count=pending.length,months=new Set(pending.map(i=>i.release_key)).size,artistsCount=new Set(pending.map(i=>i.creator_id)).size;
 const label=$('availability');
 const schedule=`Next check: ${next} · Every ${data.interval_hours} hours while this tool is open.`;
 const statusText=!data.connected?'Connect your MMF account to check for downloads.':!data.settings.subscriptions.length?'Subscribe to MMF artists to check for downloads.':checking?'Checking for available downloads…':data.availability_claimed?schedule:count?`${count} files detected · ${months} releases · ${artistsCount} artists · ${schedule}`:data.job.action==='check'&&['error','interrupted'].includes(data.job.phase)?'Availability check incomplete · '+schedule:(data.checked_at?'Check complete — no new files found · ':'No downloads detected yet · ')+schedule;
 if(label.textContent!==statusText){
  const index=count&&!checking&&data.connected&&data.settings.subscriptions.length?statusText.indexOf('detected'):-1;
  if(index>=0){const word=text('span','detected');word.className='detected-flash';label.replaceChildren(document.createTextNode(statusText.slice(0,index)),word,document.createTextNode(statusText.slice(index+8)))}else label.textContent=statusText;
 }

 const job=data.job;$('job').hidden=!job.phase;$('job-message').textContent=job.message||'';$('job-message').classList.toggle('error',['error','interrupted'].includes(job.phase));$('progress').hidden=!data.active;if(job.expected>0)$('progress').value=Math.min(100,100*(job.bytes||0)/job.expected);else if(job.total>0)$('progress').value=Math.min(100,100*(job.done||0)/job.total);else $('progress').removeAttribute('value');$('metrics').textContent=[job.phase==='processing_pdfs'?'Processing PDF footer stamps':job.phase,job.image_total!=null?`${job.image_count||0} / ${job.image_total} images`:'',data.active&&job.expected?((job.progress_unit==='percent'||['unpacking','repacking','verifying_archive'].includes(job.phase))?`${job.bytes||0}%`:`${((job.bytes||0)/1e6).toFixed(1)} / ${(job.expected/1e6).toFixed(1)} MB`):'',data.active&&job.speed_mbps?`${job.speed_mbps} MB/s`:''].filter(Boolean).join(' · ');$('errors').replaceChildren(...(job.errors||[]).map(e=>{const p=text('p',`${e.creator} · ${e.release}: ${e.error}`);p.className='error';return p}));for(const warning of job.warnings||[])$('errors').append(text('p','Warning: '+warning));queue();
}
async function refresh(reset=false){if(inFlight)return;inFlight=true;try{const oldActive=data?.active;data=await request('');render(reset||oldActive!==data.active&&!dirty)}catch(e){notice(e.message,true)}finally{inFlight=false}}
async function act(action,payload={}){try{notice('Working…');await request(action,payload);notice(action==='save'?'Subscriptions saved.':'');if(action==='save'){dirty=false}await refresh(action==='save'||action==='login')}catch(e){notice(e.message,true)}}
$('save').onclick=()=>act('save',{revision:data.settings.revision,subscriptions:draft});for(const action of ['check','download','resume','stop'])$(action).onclick=()=>act(action);
$('search').oninput=()=>{artistPage=0;artists()};$('subscription-filter').onchange=()=>{artistPage=0;artists()};$('artists-previous').onclick=()=>{artistPage--;artists()};$('artists-next').onclick=()=>{artistPage++;artists()};$('filter').onchange=()=>{page=0;queue()};$('previous').onclick=()=>{page--;queue()};$('next').onclick=()=>{page++;queue()};refresh(true);let lastRefresh=0;setInterval(()=>{const now=Date.now();if(now-lastRefresh>=(data?.active?500:3000)){lastRefresh=now;refresh()}},500);
})();
