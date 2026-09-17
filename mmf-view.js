(()=>{
const $=id=>document.getElementById(id);let data=null,draft=[],dirty=false,page=0,inFlight=false;
const expandedReleases=new Set();let queueSignature='';
const text=(tag,value)=>{const n=document.createElement(tag);n.textContent=value;return n};
async function request(action,payload){const r=await fetch('/api/mmf'+(action?'/'+action:''),{cache:'no-store',...(payload!==undefined?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{})});const value=await r.json();if(!r.ok)throw Error(value.error||'Request failed');return value}
function notice(value,error=false){$('notice').textContent=value;$('notice').className=error?'error':''}
function changed(){dirty=JSON.stringify(draft)!==JSON.stringify(data.settings.subscriptions);$('save').disabled=!dirty||data.active}
function artists(){const body=$('artists');body.replaceChildren();const search=$('search').value.toLowerCase();
 for(const creator of data.creators.filter(c=>c.name.toLowerCase().includes(search))){let sub=draft.find(s=>s.id===creator.id);const row=document.createElement('tr'),toggle=document.createElement('input');toggle.type='checkbox';toggle.className='toggle';toggle.checked=!!sub;toggle.setAttribute('role','switch');toggle.setAttribute('aria-label','Subscribe to '+creator.name);toggle.disabled=data.active;
 const folder=document.createElement('input');folder.setAttribute('list','folders');folder.setAttribute('aria-label',creator.name+' folder');folder.value=sub?.folder||data.folders.find(f=>f.replace(/^[-\s]+/,'').toLowerCase()===creator.name.toLowerCase())||creator.name;
 const scope=document.createElement('select');scope.setAttribute('aria-label',creator.name+' scope');scope.append(new Option('From month','month'),new Option('All (archiving)','all'));scope.value=sub&&sub.start_month===''?'all':'month';const month=document.createElement('input');month.type='month';month.setAttribute('aria-label',creator.name+' starting month');const now=new Date();now.setDate(1);now.setMonth(now.getMonth()-1);month.value=sub?.start_month||`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}`;
 const enable=()=>{folder.disabled=scope.disabled=month.disabled=!toggle.checked||data.active;month.hidden=scope.value==='all'};enable();
 const update=()=>{if(toggle.checked){const old=draft.findIndex(s=>s.id===creator.id);const entry={id:creator.id,name:creator.name,folder:folder.value,start_month:scope.value==='all'?'':month.value};if(old<0)draft.push(entry);else draft[old]=entry}else draft=draft.filter(s=>s.id!==creator.id);enable();changed()};toggle.onchange=folder.oninput=scope.onchange=month.oninput=update;
 for(const node of [toggle,text('span',creator.name),folder]){const td=document.createElement('td');td.append(node);row.append(td)}const td=document.createElement('td');td.append(scope,month);row.append(td);body.append(row);
 }}
function releaseGroups(){
 const groups=new Map();
 for(const item of data.items){const key=JSON.stringify([item.creator_id,item.release_id?['id',String(item.release_id)]:item.release?['label',item.release]:['object',item.object_id]]);let group=groups.get(key);if(!group){group={key,creator:item.creator,name:item.release||item.object_name,items:[]};groups.set(key,group)}group.items.push(item)}
 return [...groups.values()].map(g=>({...g,size:g.items.reduce((sum,i)=>sum+i.size,0),recorded:g.items.filter(i=>i.completed).length,review:g.items.filter(i=>!i.completed&&!i.month).length,months:[...new Set(g.items.map(i=>i.release_month).filter(Boolean))].sort()})).sort((a,b)=>a.creator.localeCompare(b.creator)||(b.months.at(-1)||'').localeCompare(a.months.at(-1)||'')||a.name.localeCompare(b.name));
}
function sizeLabel(bytes){return bytes>=1e9?(bytes/1e9).toFixed(2)+' GB':(bytes/1e6).toFixed(1)+' MB'}
function queue(){
 const groups=releaseGroups(),rows=groups.filter(g=>$('filter').value==='all'||($('filter').value==='done'?g.recorded===g.items.length:g.recorded<g.items.length));
 page=Math.min(page,Math.max(0,Math.ceil(rows.length/50)-1));
 const signature=JSON.stringify([data.items,data.active,$('filter').value,page]);if(signature===queueSignature)return;queueSignature=signature;$('queue').replaceChildren();
 for(const group of rows.slice(page*50,page*50+50)){
  const details=document.createElement('details');details.className='release-row';details.open=expandedReleases.has(group.key);details.ontoggle=()=>{if(!details.isConnected)return;if(details.open)expandedReleases.add(group.key);else expandedReleases.delete(group.key)};
  const summary=document.createElement('summary'),heading=document.createElement('span');heading.className='release-heading';heading.append(text('strong',group.name),text('small',group.creator));
  const info=document.createElement('span');info.className='release-info';info.append(text('span',`${group.items.length} ${group.items.length===1?'file':'files'} · ${sizeLabel(group.size)}`));
  const first=group.items[0];info.append(text('small',(first.release_folder||group.name)+(first.release_month_basis==='created_at'?' · from MMF creation date':'')));
  const status=text('span',group.recorded===group.items.length?'Recorded':group.recorded?`${group.recorded}/${group.items.length} recorded`:'Available');status.className='release-status '+(group.recorded===group.items.length?'recorded':'');summary.append(heading,info,status);details.append(summary);
  const box=document.createElement('div');box.className='release-files table';const table=document.createElement('table'),head=document.createElement('thead'),header=document.createElement('tr');for(const label of ['File','Size','Status'])header.append(text('th',label));head.append(header);table.append(head);const body=document.createElement('tbody');
  for(const item of group.items){
   const row=document.createElement('tr'),name=document.createElement('td');name.append(text('span',item.filename));if(item.object_name&&item.object_name!==group.name)name.append(text('small',item.object_name));row.append(name,text('td',sizeLabel(item.size)));row.append(text('td',item.completed?'Downloaded':'Ready'));body.append(row)
  }
  table.append(body);box.append(table);details.append(box);$('queue').append(details)
 }
 $('page').textContent=rows.length?`${page+1} / ${Math.ceil(rows.length/50)} · ${rows.length} releases`:'No releases to show';$('previous').disabled=page===0;$('next').disabled=(page+1)*50>=rows.length;
}
function render(reset=false){$('connection').hidden=data.connected;$('sub-count').textContent=`${data.settings.subscriptions.length} subscribed · ${data.creators.length} artists`;
 $('folders').replaceChildren(...data.folders.map(f=>new Option(f,f)));if(reset||!draft.length&&!dirty){draft=structuredClone(data.settings.subscriptions);artists()}
 for(const id of ['check','download','resume'])$(id).disabled=data.active||!data.connected||dirty;
 $('resume').disabled ||= !data.resumable;
 $('download').disabled ||= !data.counts.available||!data.checked_at;$('stop').disabled=!data.active;$('save').disabled=!dirty||data.active;
 const next=data.next_check_at?new Date(data.next_check_at*1000).toLocaleString():'Due now';const releases=releaseGroups();const pending=releases.filter(g=>g.recorded<g.items.length).length;$('availability').textContent=`${pending} releases available · ${releases.length-pending} recorded · Next check: ${next}`;
 const job=data.job;$('job').hidden=!job.phase;$('job-message').textContent=job.message||'';$('progress').hidden=!data.active;$('progress').value=job.expected?100*(job.bytes||0)/job.expected:job.total?100*(job.done||0)/job.total:0;$('metrics').textContent=[job.phase,data.active&&job.expected?(['unpacking','repacking','verifying_archive'].includes(job.phase)?`${job.bytes||0}%`:`${((job.bytes||0)/1e6).toFixed(1)} / ${(job.expected/1e6).toFixed(1)} MB`):'',data.active&&job.speed_mbps?`${job.speed_mbps} MB/s`:''].filter(Boolean).join(' · ');$('errors').replaceChildren(...(job.errors||[]).map(e=>{const p=text('p',`${e.creator} · ${e.release}: ${e.error}`);p.className='error';return p}));for(const warning of job.warnings||[])$('errors').append(text('p','Warning: '+warning));queue();
}
async function refresh(reset=false){if(inFlight)return;inFlight=true;try{const oldActive=data?.active;data=await request('');render(reset||oldActive!==data.active&&!dirty)}catch(e){notice(e.message,true)}finally{inFlight=false}}
async function act(action,payload={}){try{notice('Working…');await request(action,payload);notice(action==='save'?'Subscriptions saved.':'');if(action==='save'){dirty=false}await refresh(action==='save'||action==='login')}catch(e){notice(e.message,true)}}
$('save').onclick=()=>act('save',{revision:data.settings.revision,subscriptions:draft});for(const action of ['check','download','resume','stop'])$(action).onclick=()=>act(action);
$('search').oninput=artists;$('filter').onchange=()=>{page=0;queue()};$('previous').onclick=()=>{page--;queue()};$('next').onclick=()=>{page++;queue()};refresh(true);setInterval(()=>refresh(),3000);
})();
