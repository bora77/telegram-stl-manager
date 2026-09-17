function element(tag,text){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el}
const subscriptionFilter=document.getElementById('subscription-filter');
function setSaveStatus(message,error=false){
  const notice=document.getElementById('save-status');
  notice.classList.toggle('save-error',error);
  notice.setAttribute('role',error?'alert':'status');
  notice.textContent=message;
}
function persist(){
  try{localStorage.setItem('telegram-stl-selection',JSON.stringify(selected))}catch{}
  setSaveStatus(hasSubscriptionChanges()?'Unsaved changes. Press Save subscriptions to apply them.':'');
  renderSelections();
}
function hasSubscriptionChanges(){
  if(Object.keys(selected).length!==savedSubscriptions.length)return true;
  return savedSubscriptions.some(record=>{
    const saved=SelectionRules.migrate({...record,selection_version:2},inventory),draft=selected[saved.topic_url];
    return !draft||draft.creator_folder!==saved.creator_folder||draft.layout!==saved.layout||
      draft.download_scope!==saved.download_scope||
      (draft.download_scope==='from_month'&&draft.start_month!==saved.start_month);
  });
}
function renderSelections(){
  document.getElementById('selected-count').textContent=`${Object.keys(selected).length} creators selected`;
  document.getElementById('save-subscriptions').disabled=!serverReady||saving||!hasSubscriptionChanges();
}
function subscriptionMatches(url, mode, saved){return !mode||(mode==='subscribed'?saved.has(url):!saved.has(url))}
function render(reset=false){
  if(reset)offset=0;
  const saved=new Set(savedSubscriptions.map(r=>r.topic_url)),q=search.value.toLocaleLowerCase();
  filtered=all.filter(r=>r.name.toLocaleLowerCase().includes(q)&&subscriptionMatches(r.topic_url,subscriptionFilter.value,saved));
  if(offset>=filtered.length)offset=Math.max(0,Math.floor((filtered.length-1)/50)*50);
  const body=document.getElementById('rows');body.replaceChildren();
  for(const r of filtered.slice(offset,offset+50)){
    const row=element('tr'),choice=element('td'),creator=element('td'),folderCell=element('td'),scopeCell=element('td');
    const draft=selected[r.topic_url],isSaved=saved.has(r.topic_url);
    const toggle=element('label'),track=element('span');toggle.className='subscription-toggle';track.className='subscription-track';track.setAttribute('aria-hidden','true');
    const checkbox=element('input');checkbox.type='checkbox';checkbox.checked=!!draft;checkbox.value=r.topic_url;checkbox.setAttribute('role','switch');checkbox.setAttribute('aria-label','Subscribe to '+r.name);
    toggle.append(checkbox,track);
    checkbox.onchange=()=>{
      const hadFocus=document.activeElement===checkbox;
      if(checkbox.checked){
        const saved=savedSubscriptions.find(record=>record.topic_url===r.topic_url);
        selected[r.topic_url]=saved?SelectionRules.migrate({...saved,selection_version:2},inventory):{selection_version:2,creator:r.name,topic_url:r.topic_url,creator_folder:SelectionRules.suggest(r.name,inventory.folders),layout:'monthly',download_scope:'from_month',start_month:SelectionRules.defaultStartMonth()};
      }
      else delete selected[r.topic_url];
      persist();render();
      if(hadFocus)Array.from(body.querySelectorAll('input[role="switch"]')).find(input=>input.value===r.topic_url)?.focus({preventScroll:true});
    };
    choice.append(toggle);
    if(!!draft!==isSaved){const membership=element('small',isSaved?'Removal pending':'Not saved yet');membership.className='changed';choice.append(membership)}
    const artistHeading=element('div');artistHeading.className='artist-heading';
    artistHeading.append(ArtistProfiles.badge(r.name),element('strong',r.name));creator.append(artistHeading);
    const profileLink=element('button','Creator links / logo');profileLink.type='button';profileLink.className='artist-profile-link';profileLink.onclick=()=>ArtistProfiles.show(r.name);creator.append(profileLink);
    const details=element('details');details.append(element('summary',r.ocr_confidence<80?'Check spelling · source details':'Source details'),element('code',r.topic_url));
    if(r.aliases?.length)details.append(element('small','Other readings: '+r.aliases.map(a=>a.name).join(', ')));
    const evidenceUrl=r.toc_source_url||r.evidence;
    if(evidenceUrl){const evidence=element('a',r.toc_source_url?'View TOC entry':'View source screenshot');evidence.href=evidenceUrl;evidence.target='_blank';evidence.rel='noopener';details.append(evidence)}creator.append(details);
    const folder=element('input');folder.type='text';folder.setAttribute('list','incoming-folders');folder.setAttribute('aria-label',r.name+' creator folder');folder.placeholder='Choose or type a folder';folder.value=draft?.creator_folder||SelectionRules.suggest(r.name,inventory.folders);folder.disabled=!draft;
    const destination=element('small');
    function updateDestination(){destination.textContent=folder.value?folder.value+'/release folder/filename · '+(inventory.folders.includes(folder.value)?'existing creator folder':'new creator folder'):'Select creator, then choose its folder.'}
    folder.oninput=()=>{draft.creator_folder=folder.value;persist();updateDestination()};updateDestination();folderCell.append(folder,destination);
    if(draft?.legacy_directory&&!draft.creator_folder)folderCell.append(element('small','Previous folder: '+draft.legacy_directory+' — please reassign.'));
    const scope=element('select');scope.setAttribute('aria-label',r.name+' download scope');
    for(const [value,label] of [['from_month','From month/year'],['all_and_future','All (archiving)']])scope.add(new Option(label,value));
    scope.value=draft?.download_scope||'from_month';scope.disabled=!draft;
    const month=element('input');month.type='month';month.min='2000-01';month.max='2099-12';month.setAttribute('aria-label',r.name+' starting month and year');month.value=draft?.start_month||'';
    const scopeHint=element('small');
    function updateScope(){month.hidden=scope.value!=='from_month';month.disabled=!draft||scope.value!=='from_month';scopeHint.textContent=scope.value==='from_month'?'Includes the chosen release month and all later releases.':scope.value==='all_and_future'?'All existing and later releases.':''}
    scope.onchange=()=>{draft.download_scope=scope.value;if(scope.value==='from_month'&&!draft.start_month){draft.start_month=SelectionRules.defaultStartMonth();month.value=draft.start_month}persist();updateScope()};month.oninput=()=>{draft.start_month=month.value;persist()};updateScope();scopeCell.append(scope,month,scopeHint);

    row.append(choice,creator,folderCell,scopeCell);body.append(row);
  }
  document.getElementById('count').textContent=`${filtered.length} matching creators · subscription filter uses saved subscriptions`;
  document.getElementById('page').textContent=filtered.length?`${offset+1}–${Math.min(offset+50,filtered.length)}`:'No matches';
  document.getElementById('previous').disabled=offset===0;document.getElementById('next').disabled=offset+50>=filtered.length;
}
for(const field of [subscriptionFilter])field.addEventListener('change',()=>render(true));
search.addEventListener('input',()=>render(true));
document.getElementById('previous').onclick=()=>{offset=Math.max(0,offset-50);render()};document.getElementById('next').onclick=()=>{offset+=50;render()};
render();renderSelections();
