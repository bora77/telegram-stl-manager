/* Completion alerts shared by every page, without starting or changing jobs. */
(()=>{
  const stateKey='telegram-stl-task-alerts-v1',soundKey='telegram-stl-completion-sound';
  const kinds={download:{tab:'queue',label:'Download run finished'},organize_preview:{tab:'organize',label:'Folder preview finished'},organize_apply:{tab:'organize',label:'Folder organization finished'}};
  const pulses=new Map(),ackTimers=new Map();
  let memory={observed:{},alerts:{}},storageAvailable=true,audio=null,pending=false;
  function readState(){
    try{const saved=JSON.parse(localStorage.getItem(stateKey)||'null');if(saved&&saved.observed&&saved.alerts)memory=saved}
    catch{storageAvailable=false}
    return memory;
  }
  function soundEnabled(){try{return localStorage.getItem(soundKey)!=='off'}catch{return false}}
  function soundStatus(){
    const select=document.getElementById('completion-sound'),status=document.getElementById('completion-sound-status');
    if(select)select.value=soundEnabled()?'on':'off';
    if(status)status.textContent=!soundEnabled()?'Completion sound is off.':!storageAvailable?'Browser storage is unavailable for completion sounds.':audio?.state==='running'?'Sound is ready.':'Click anywhere in the page or use Test sound to allow audio.';
  }
  async function changeState(change){
    const update=()=>{
      const state=readState(),before=JSON.stringify(state),result=change(state),after=JSON.stringify(state);
      if(after!==before){try{localStorage.setItem(stateKey,after)}catch{storageAvailable=false}}
      render(state);return result;
    };
    return navigator.locks?navigator.locks.request(stateKey,update):update();
  }
  function chime(){
    if(audio?.state!=='running')return false;
    try{
      const start=audio.currentTime;
      for(const [offset,frequency] of [[0,659.25],[.18,880]]){
        const oscillator=audio.createOscillator(),gain=audio.createGain();
        oscillator.type='sine';oscillator.frequency.value=frequency;
        gain.gain.setValueAtTime(0,start+offset);gain.gain.linearRampToValueAtTime(.12,start+offset+.025);gain.gain.exponentialRampToValueAtTime(.001,start+offset+.24);
        oscillator.connect(gain);gain.connect(audio.destination);oscillator.start(start+offset);oscillator.stop(start+offset+.25);
        oscillator.onended=()=>{oscillator.disconnect();gain.disconnect()};
      }
      return true;
    }catch{return false}
  }
  async function soundPending(){
    if(!soundEnabled()||!storageAvailable||audio?.state!=='running')return;
    // Older browsers without Web Locks only let the focused page claim sound.
    if(!navigator.locks&&(!document.hasFocus()||document.visibilityState!=='visible'))return;
    await changeState(state=>{
      if(!soundEnabled()||audio?.state!=='running')return;
      // Background tabs may have their polling delayed by the browser.
      const fresh=Object.values(state.alerts).filter(alert=>!alert.sounded&&Date.now()-alert.at<300000);
      if(fresh.length&&chime())for(const alert of fresh)alert.sounded=true;
    });
  }
  async function enableAudio(){
    if(!soundEnabled())return;
    try{
      const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)return;
      if(!audio){audio=new Audio();audio.onstatechange=soundStatus}
      await audio.resume();soundStatus();await soundPending();
    }catch{soundStatus()}
  }
  function currentTab(){return document.querySelector('.app-navigation a[aria-current="page"]')?.id.replace(/-tab$/,'')}
  async function acknowledge(tab,tokens){
    await changeState(state=>{for(const [kind,alert] of Object.entries(state.alerts))if(kinds[kind]?.tab===tab&&tokens.includes(alert.token))alert.seen=true});
  }
  function render(state){
    for(const tab of ['queue','organize']){
      const link=document.getElementById(tab+'-tab');if(!link)continue;
      const alerts=Object.entries(state.alerts).filter(([kind,alert])=>kinds[kind]?.tab===tab&&!alert.seen).map(([,alert])=>alert);
      link.classList.toggle('task-notice',alerts.length>0);
      link.dataset.taskWarning=String(alerts.some(alert=>alert.warning));
      if(!alerts.length){link.classList.remove('task-flash');link.removeAttribute('title');link.removeAttribute('aria-label');continue}
      const message=alerts.map(alert=>alert.message).join(' · '),token=alerts.map(alert=>alert.token).join('|');
      link.title=message;link.setAttribute('aria-label',link.textContent+' — '+message);
      if(pulses.get(tab)!==token){
        pulses.set(tab,token);link.classList.remove('task-flash');void link.offsetWidth;link.classList.add('task-flash');
        document.getElementById('task-completion-announcement').textContent=message;
      }
      if(currentTab()===tab&&document.visibilityState==='visible'&&ackTimers.get(tab)!==token){
        ackTimers.set(tab,token);const tokens=alerts.map(alert=>alert.token);
        setTimeout(()=>{if(currentTab()===tab&&document.visibilityState==='visible')acknowledge(tab,tokens);if(ackTimers.get(tab)===token)ackTimers.delete(tab)},6500);
      }
    }
    soundStatus();
  }
  async function refresh(){
    if(pending)return;pending=true;
    try{
      const response=await fetch('/api/tasks',{cache:'no-store'});if(!response.ok)return;
      const data=await response.json();
      await changeState(state=>{
        for(const [kind,info] of Object.entries(kinds)){
          const task=data.tasks?.[kind];if(!task)continue;
          const previous=state.observed[kind];
          // A delayed poll from another tab must not replace a newer snapshot.
          if(previous?.version&&task.version&&BigInt(task.version)<BigInt(previous.version))continue;
          const finished=['completed','needs_review'].includes(task.state)&&task.id&&task.finished_at;
          const token=finished?JSON.stringify([kind,task.id,task.finished_at]):null;
          // The first snapshot is a baseline, so opening the app never rings for
          // old jobs. IDs plus finish times distinguish retries and new runs.
          if(previous&&token&&previous.token!==token){
            const warning=Boolean(task.needs_review);
            state.alerts[kind]={token,at:Date.parse(task.finished_at)||Date.now(),warning,seen:false,sounded:!soundEnabled(),message:info.label+(task.creator?' · '+task.creator:'')+(warning?' · review warnings':'')};
          }
          // Reclassified saved warnings update an existing badge without
          // creating another completion event or replaying its sound.
          if(token&&state.alerts[kind]?.token===token){
            const warning=Boolean(task.needs_review);
            Object.assign(state.alerts[kind],{warning,message:info.label+(task.creator?' · '+task.creator:'')+(warning?' · review warnings':'')});
          }
          if(!finished&&task.id&&(task.id!==previous?.id||previous?.token))delete state.alerts[kind];
          state.observed[kind]={id:task.id,state:task.state,token,version:task.version};
        }
      });
      await soundPending();
    }catch{/* A failed poll is not a completed task. Keep the previous snapshot. */}
    finally{pending=false}
  }
  function start(){
    const select=document.getElementById('completion-sound'),test=document.getElementById('test-completion-sound');
    if(select)select.onchange=()=>{try{localStorage.setItem(soundKey,select.value)}catch{storageAvailable=false}soundStatus();if(select.value==='on')enableAudio()};
    if(test)test.onclick=async()=>{
      try{
        const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)throw Error();
        if(!audio){audio=new Audio();audio.onstatechange=soundStatus}
        await audio.resume();
        document.getElementById('completion-sound-status').textContent=chime()?'Test sound played.':'Sound is blocked by this browser.';
      }catch{document.getElementById('completion-sound-status').textContent='Sound is unavailable in this browser.'}
    };
    document.addEventListener('pointerdown',enableAudio,{passive:true});document.addEventListener('keydown',enableAudio);
    for(const tab of ['queue','organize'])document.getElementById(tab+'-tab')?.addEventListener('click',()=>{
      const state=readState(),tokens=Object.entries(state.alerts).filter(([kind])=>kinds[kind]?.tab===tab).map(([,alert])=>alert.token);acknowledge(tab,tokens);
    });
    window.addEventListener('storage',event=>{if(event.key===stateKey||event.key===soundKey){render(readState());soundPending()}});
    document.addEventListener('visibilitychange',()=>{render(readState());if(document.visibilityState==='visible')refresh()});
    window.addEventListener('hashchange',()=>render(readState()));
    render(readState());enableAudio();refresh();setInterval(refresh,3000);
  }
  window.TaskNotifications=Object.freeze({refresh});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();


/* Open browsers schedule checks; the web server has no recurring timer. */
(()=>{
 let busy=false;
 async function pollAvailability(){
  if(busy)return;busy=true;
  const label=document.getElementById('availability-status');
  try{
   let response=await fetch('/api/availability',{cache:'no-store'});
   if(response.status===403){const blocked=await response.json();if(blocked.setup_required){if(label)label.hidden=true;return}}
   if(!response.ok)throw Error();let state=await response.json();
   if(state.subscribed&&!state.checking&&Date.now()/1000>=state.next_check_at){
    response=await fetch('/api/availability/check',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    if(!response.ok)throw Error();state=await response.json();
   }
   if(label){
    label.hidden=!state.subscribed;
    const result=state.files?`${state.files} files available · ${state.releases} releases · ${state.creators} artists`:(state.errors.length?'Availability check incomplete':'No downloads available');
    label.textContent=state.checking?'Checking for available downloads…':state.deferred?'Next check: due now · Waiting for the current task to finish.':
     `${result}${state.checked_at?' · Checked '+new Date(state.checked_at*1000).toLocaleString():''} · Next check: ${new Date(state.next_check_at*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})} · Every ${state.interval_seconds/3600} hours while this tool is open.`;
    if(state.errors.length&&state.files)label.textContent+=' · Some artists could not be checked.';
   }
  }catch{if(label){label.hidden=false;label.textContent='Availability check unavailable; the browser will retry.'}}
  finally{busy=false}
 }
 function start(){window.addEventListener('availability-config-saved',pollAvailability);pollAvailability();setInterval(pollAvailability,60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollAvailability()})}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();

/* MMF beta is opt-in. Hidden by default, including automatic checks. */
(()=>{
 window.mmfBetaEnabled=false;
 function apply(config){const enabled=config.mmf_beta_enabled===true;window.mmfBetaEnabled=enabled;const tab=document.getElementById('mmf-tab'),account=document.getElementById('mmf-account');if(tab)tab.hidden=!enabled;if(account)account.hidden=!enabled;window.dispatchEvent(new Event('mmf-beta-changed'))}
 async function refresh(){try{const r=await fetch('/api/config',{cache:'no-store'});if(r.ok)apply(await r.json())}catch{}}
 window.addEventListener('beta-config-saved',e=>apply(e.detail));
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});
 refresh();setInterval(refresh,60000);
})();
/* MMF availability is requested only while a manager page is open. */
(()=>{let busy=false;async function check(){if(busy||!window.mmfBetaEnabled)return;busy=true;try{const response=await fetch('/api/mmf/status',{cache:'no-store'});if(!response.ok)return;const state=await response.json();const link=document.getElementById('mmf-tab');if(link)link.title=state.counts.available+' MMF files available';if(state.connected&&!state.active&&state.settings.subscriptions.length&&Date.now()/1000>=state.next_check_at)await fetch('/api/mmf/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({due:true})})}catch{}finally{busy=false}}check();setInterval(check,60000)})();

/* Initial setup is also enforced by the server for direct links and APIs. */
(()=>{
 function apply(state){for(const link of document.querySelectorAll('.app-navigation a:not(#config-tab)')){if(state.setup_required){link.setAttribute('aria-disabled','true');link.tabIndex=-1;link.title='Complete initial setup first'}else{link.removeAttribute('aria-disabled');link.removeAttribute('tabindex');if(link.title==='Complete initial setup first')link.removeAttribute('title')}}}
 window.addEventListener('initial-setup-state',e=>apply(e.detail));
 document.addEventListener('click',e=>{const link=e.target.closest('.app-navigation a[aria-disabled=true]');if(link){e.preventDefault();document.getElementById('setup-checklist')?.scrollIntoView({behavior:'smooth'})}});
 fetch('/api/setup',{cache:'no-store'}).then(r=>r.json()).then(apply).catch(()=>{});
})();
