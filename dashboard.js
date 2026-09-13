async function api(path, data) {
  const response=await fetch(path,{cache:'no-store',...(data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})});
  const body=await response.json();if(!response.ok)throw Error(body.error||'Request failed');return body;
}
function savedSummary(){document.getElementById('saved-summary').textContent=`${savedSubscriptions.length} subscriptions saved · manual downloads only`;}
function applySaved(){
  selected={};for(const r of savedSubscriptions)selected[r.topic_url]=SelectionRules.migrate({...r,selection_version:2},inventory);
  try{localStorage.setItem('telegram-stl-selection',JSON.stringify(selected))}catch{}
  render();renderSelections();setSaveStatus('Loaded saved subscriptions.');
}
document.getElementById('load-subscriptions').onclick=async()=>{
  try{const data=await api('/api/subscriptions');savedSubscriptions=data.subscriptions;serverRevision=data.revision;const config=await api('/api/config');configRevision=config.revision;downloadBase=config.download_directory;document.getElementById('download-base').textContent=downloadBase;applySaved();savedSummary()}
  catch(error){setSaveStatus(error.message,true)}
};
document.getElementById('save-subscriptions').onclick=async()=>{
  const entries=Object.values(selected),allowed=new Set(all.map(r=>r.topic_url));
  const bad=entries.find(r=>SelectionRules.validate(r,allowed));
  if(bad){setSaveStatus('Not saved: '+bad.creator+': '+SelectionRules.validate(bad,allowed),true);return}
  const snapshot=JSON.stringify(selected);saving=true;renderSelections();
  setSaveStatus('Saving subscriptions…');
  try{
    const data=await api('/api/subscriptions',{revision:serverRevision,config_revision:configRevision,subscriptions:entries.map(SelectionRules.exportRecord)});
    serverRevision=data.revision;savedSubscriptions=data.subscriptions;savedSummary();render();
    setSaveStatus(JSON.stringify(selected)===snapshot?'Subscriptions saved. Press Download all subscriptions when ready.':'Subscriptions saved, but you have newer unsaved edits.');
    refreshQueue();
  }catch(error){setSaveStatus('Not saved: '+error.message,true)}
  finally{saving=false;renderSelections()}
};
function bytes(value){if(value==null)return 'Size pending';const units=['B','KiB','MiB','GiB','TiB'];let i=0;while(value>=1024&&i<units.length-1){value/=1024;i++}return value.toFixed(i?1:0)+' '+units[i]}
function duration(value){if(value==null)return 'Timing unavailable';const secs=Math.max(0,Math.round(value));return secs<60?secs+' s':Math.floor(secs/60)+' min '+secs%60+' s'}
function speed(value){return value==null?'Speed unavailable':(value/1e6).toFixed(1)+' MB/s'}
function progress(bar,done,total){if(total>0){bar.value=Math.min(100,100*done/total)}else if(total===0){bar.value=0}else{bar.removeAttribute('value')}}
function runMessage(run){
  const message=String(run.message||'');
  const missing=message.match(/^ERROR = Missing volume : ([^\r\n]+)/m);
  if(missing)return 'Image extraction stopped: missing archive part '+missing[1]+'. Downloaded files have been kept locally.';
  return message;
}
function renderRunActivity(queue){
  const run=queue.run,state=queue.worker_state,ended=['completed','needs_review'].includes(state);
  document.getElementById('run-activity').dataset.active=String(queue.active);
  const phase=({starting:'Starting run',scanning:'Checking Telegram files',downloading:'Downloading file',extracting:'Extracting release images',copying:'Moving file to destination',completed:'Run finished',needs_review:'Run finished · review needed',failed:'Run stopped with an error',interrupted:'Run interrupted',stopped:'Run stopped'})[state]||'Ready';
  document.getElementById('run-phase').textContent=phase;
  const seconds=run.started_at?(Date.parse(run.finished_at||new Date().toISOString())-Date.parse(run.started_at))/1000:null;
  document.getElementById('run-elapsed').textContent=seconds==null?'':(queue.active?'Elapsed: ':'Run duration: ')+duration(seconds);
  const scanning=state==='scanning';
  document.getElementById('run-detail').textContent=scanning?`${run.creators_checked||0} / ${run.creators_total||0} artists checked · ${run.message||'Checking new messages…'}${run.files_listed?' · '+run.files_listed+' new file records':''}`:ended&&queue.total_files===0?'No matching releases were queued for download.'+(run.warnings?.length?' Some entries could not be classified; review them below.':''):runMessage(run);
  document.getElementById('review-summary').textContent=`Items needing review (${run.warnings?.length||0})`;
}
async function refreshQueue(){
  try{
    const queue=await api('/api/queue');
    renderRunActivity(queue);
    const checking=['starting','scanning'].includes(queue.worker_state),emptyFinished=['completed','needs_review'].includes(queue.worker_state)&&queue.total_files===0;
    const stopped=['failed','interrupted','stopped'].includes(queue.worker_state);
    document.getElementById('worker-state').textContent=queue.worker_state.replaceAll('_',' ');
    document.getElementById('download-all').disabled=!queue.can_start||saving;document.getElementById('stop-downloads').disabled=!queue.active;
    document.getElementById('download-start-help').textContent=queue.active?'One manual run is active.':stopped?'Press Download all subscriptions to retry. Completed files are skipped and verified local downloads are reused.':queue.subscriptions_saved?'Runs all saved subscriptions; unsaved edits are excluded.':'Save at least one subscription to start.';
    const total=queue.progress_total??queue.bytes_total;
    const percent=total>0?Math.min(100,100*queue.bytes_downloaded/total):null;
    document.getElementById('queue-percent').textContent=stopped?'Stopped'+(percent==null?'':' at '+Math.floor(percent)+'%'):checking?'Checking files…':emptyFinished?'No files queued':percent==null?(queue.total_files?'—':'0%'):Math.floor(percent)+'%';
    const overall=document.getElementById('queue-progress');overall.hidden=emptyFinished;
    progress(overall,queue.bytes_downloaded,checking?null:queue.total_files?total:0);
    document.getElementById('queue-files').textContent=`${queue.completed_files} / ${queue.total_files} files complete`;
    document.getElementById('history-summary').textContent=`${queue.history_completed||0} completed downloads in permanent history · retained when files move`;
    document.getElementById('queue-bytes').textContent=queue.total_files?bytes(queue.bytes_downloaded)+' / '+(queue.total_is_estimate?'≈ ':'')+bytes(total):checking?'Scanning before downloading':emptyFinished?'No data transferred':'No transfers yet';
    document.getElementById('queue-message').textContent=runMessage(queue.run)+(queue.worker_state==='downloading'&&queue.run.download_server?' · Server: '+queue.run.download_server:'');
    const warnings=document.getElementById('run-warnings');warnings.replaceChildren();for(const text of queue.run.warnings||[])warnings.append(element('li',text));
    const moving=queue.worker_state==='copying';document.getElementById('move-panel').hidden=!moving;
    const extracting=queue.worker_state==='extracting';document.getElementById('extraction-panel').hidden=!extracting;
    const extraction=queue.run;
    const checkingParts=extraction.extraction_stage==='checking_parts',listingArchive=extraction.extraction_stage==='listing',preparingParts=extraction.extraction_stage==='preparing_parts';
    document.getElementById('extraction-title').textContent=checkingParts?'Checking split archive integrity':listingArchive?'Reading archive contents':preparingParts?'Preparing archive parts':'Extracting release images locally';
    document.getElementById('extraction-help').textContent=checkingParts?'7-Zip checks all archive parts before extracting images. Large compressed releases can take several minutes.':listingArchive?'Reading the archive’s file list before selecting images.':'Images go into release_images inside the monthly release folder. Archive files remain intact.';
    progress(document.getElementById('extraction-progress'),extraction.extraction_bytes||0,extraction.extraction_total||null);
    document.getElementById('extraction-status').textContent=extracting?(checkingParts?`${extraction.extraction_total?Math.round(100*extraction.extraction_bytes/extraction.extraction_total)+'% checked · ':''}${duration(extraction.extraction_seconds)} elapsed`:listingArchive?`${duration(extraction.extraction_seconds)} elapsed`:`${preparingParts?'Preparing parts':`${extraction.images_done||0}${extraction.images_total!=null?' / '+extraction.images_total:''} images`} · ${duration(extraction.extraction_seconds)} elapsed${extraction.extraction_total?' · '+bytes(extraction.extraction_bytes)+' / '+bytes(extraction.extraction_total):''}`):'';
    const verifying=queue.run.move_stage==='verifying';
    document.getElementById('move-title').textContent=verifying?'Verifying file on Kronos':'Moving from local disk to Kronos';
    progress(document.getElementById('move-progress'),queue.run.copy_bytes||0,queue.run.copy_total||null);
    document.getElementById('move-status').textContent=moving?`${bytes(queue.run.copy_bytes)} / ${bytes(queue.run.copy_total)} · ${verifying?'Checking checksum':speed(queue.run.move_speed_bps)} · ${duration(queue.run.move_seconds)} elapsed · average ${speed(queue.run.move_average_bps)}`:'';

    const downloading=queue.worker_state==='downloading'?queue.files.find(f=>f.state==='downloading'&&f.download_finished_at==null):null;
    document.getElementById('download-speed').textContent=downloading?`Download: ${speed(downloading.download_speed_bps)} · ${duration(downloading.download_seconds)} elapsed · average ${speed(downloading.download_average_bps)}`:'';
    const list=document.getElementById('queue-list');list.replaceChildren();
    for(const file of queue.files){const row=element('div');row.className='queue-file';row.append(element('strong',file.filename),element('small',`${file.creator} · ${file.state} · ${bytes(file.bytes_downloaded)} / ${file.total_is_estimate?'≈ ':''}${bytes(file.progress_total??file.bytes_total)}`));const bar=element('progress');bar.max=100;bar.setAttribute('aria-label',file.filename+' download progress');progress(bar,file.bytes_downloaded,file.progress_total??file.bytes_total);if(file.download_started_at)row.append(element('small',`Download: ${duration(file.download_seconds)} · average ${speed(file.download_average_bps)}`));row.append(bar);list.append(row)}
    const recent=document.getElementById('recent-transfers');recent.replaceChildren();for(const file of queue.recent_completed||[]){const row=element('div');row.className='queue-file';row.append(element('strong',file.filename),element('small',file.download_finished_at?`Moved to destination · Download ${duration(file.download_seconds)} · average ${speed(file.download_average_bps)}`:'Moved to destination · Download timing was not recorded'));if(file.move_seconds!=null)row.append(element('small',`Move to destination: ${duration(file.move_seconds)} · average ${speed(file.move_average_bps)}`));if(file.image_count!=null)row.append(element('small',`${file.image_count} release images saved`));recent.append(row)}
  }catch{document.getElementById('worker-state').textContent='Status unavailable';document.getElementById('queue-message').textContent='Cannot reach the local service. Last displayed progress may be out of date.'}
}
async function initialize(){
  try{
    const [config,data]=await Promise.all([api('/api/config'),api('/api/subscriptions')]);
    configRevision=config.revision;downloadBase=config.download_directory;document.getElementById('download-base').textContent=downloadBase;
    inventory.folders=config.folders;const list=document.getElementById('incoming-folders');list.replaceChildren();for(const name of inventory.folders)list.append(new Option(name,name));
    serverRevision=data.revision;savedSubscriptions=data.subscriptions;serverReady=true;
    let hasDraft=false;try{hasDraft=localStorage.getItem('telegram-stl-selection')!==null}catch{}
    if(!hasDraft)applySaved();else {render();renderSelections()}
    subscriptionFilter.disabled=false;
    document.getElementById('load-subscriptions').disabled=false;savedSummary();
    if(!config.available)setSaveStatus('The configured download folder is currently unavailable. You can save settings, but downloads must wait.');
  }catch(error){setSaveStatus('Cannot load saved settings: '+error.message,true)}
}
initialize();refreshQueue();setInterval(refreshQueue,3000);

document.getElementById('download-all').onclick=async()=>{
  const button=document.getElementById('download-all');button.disabled=true;
  document.getElementById('run-phase').textContent='Starting run…';document.getElementById('run-detail').textContent='Sending your manual download request.';document.getElementById('run-activity').dataset.active='true';document.getElementById('queue-message').textContent='Starting…';
  try{await api('/api/run',{revision:serverRevision,config_revision:configRevision});await refreshQueue()}
  catch(error){document.getElementById('queue-message').textContent=error.message;document.getElementById('run-phase').textContent='Could not start';document.getElementById('run-detail').textContent=error.message;document.getElementById('run-activity').dataset.active='false';button.disabled=false}
};
document.getElementById('stop-downloads').onclick=async()=>{
  try{const result=await api('/api/stop',{});document.getElementById('queue-message').textContent=result.message}catch(error){document.getElementById('queue-message').textContent=error.message}
};
