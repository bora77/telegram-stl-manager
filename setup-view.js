(()=>{
const $=id=>document.getElementById(id);let state=null,busy=false;
async function call(action,payload){const r=await fetch('/api/setup'+(action?'/'+action:''),{cache:'no-store',...(payload?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{})});const d=await r.json();if(!r.ok)throw Error(d.error||'Setup failed');return d}
function message(value,error=false){const target=state?.setup_required&&step===1?$('setup-share-message'):$('setup-message');target.textContent=value;target.style.color=error?'#a12632':''}
const titles=['Telegram & artists','Download location','Preferences','Review & finish'];
let step=Math.max(0,['#setup-account','#setup-storage','#setup-preferences','#setup-finish'].indexOf(location.hash)),baseline=null,reachable=false,finishing=false;
const hashes=['#setup-account','#setup-storage','#setup-preferences','#setup-finish'];
function snapshot(){return JSON.stringify([...document.querySelectorAll('.configuration-content input,.configuration-content select')].filter(el=>el.id==='folder'||el.id==='storage-kind'||el.id==='mmf-beta-enabled'||el.id==='release-pad-destination'||el.closest('[data-setup-step="2"]')&&!el.closest('#mmf-account,#release-pad-picker')&&el.id!=='completion-sound').map(el=>[el.id||el.dataset.dc,el.type==='checkbox'?el.checked:el.value]));}
window.setupWizard={snapshot};
function issues(){
 const result=(state?.missing_setup||[]).map(text=>({text,step:/Telegram|Table of Contents/.test(text)?0:/folder/.test(text)?1:2}));
 if($('save')?.disabled)result.push({text:'Wait for configuration to finish loading or saving.',step:2});
 if(!reachable)result.push({text:'Cannot verify setup. Check the connection to the manager.',step});
 if(baseline===null)result.push({text:'Wait for configuration to load successfully.',step:2});
 else if(snapshot()!==baseline)result.push({text:'You have unsaved settings. Save configuration in Preferences.',step:2});
 if(state?.source&&$('setup-toc').value.trim()!==state.source)result.push({text:'Import the Table of Contents link you entered, or restore the saved link.',step:0});
 if($('storage-kind')?.value==='network'&&!$('setup-share').hidden&&(['path','user','domain'].some((key,i)=>$('setup-share-'+key).value!==String(state?.share?.[['path','username','domain'][i]]||''))||$('setup-share-password').value))result.push({text:'Connect and save the network share details you entered.',step:1});
 return result;
}
function renderWizard(){
 if(!$('setup-review'))return;
 const active=!!state?.setup_required;
 $('setup-checklist').hidden=!active;$('setup-review').hidden=!active;$('setup-controls').hidden=!active;
 document.querySelectorAll('[data-setup-step]').forEach(el=>el.classList.toggle('setup-step-hidden',active&&Number(el.dataset.setupStep)!==step));
 document.querySelectorAll('[data-setup-normal]').forEach(el=>el.classList.toggle('setup-step-hidden',active));
 if(!active)return;
 $('setup-step-title').textContent=`Step ${step+1} of 4 · ${titles[step]}`;
 document.querySelectorAll('[data-setup-go]').forEach(el=>{if(Number(el.dataset.setupGo)===step)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current')});
 $('setup-back').disabled=step===0||finishing;$('setup-next').hidden=step===3;$('setup-next').textContent=step===2?'Save and review':'Continue';$('setup-next').disabled=finishing||(step===2&&$('save').disabled);
 const missing=issues();$('setup-finish').disabled=finishing||missing.length>0;
 const signature=JSON.stringify(missing);
 if($('setup-missing').dataset.signature!==signature){$('setup-missing').dataset.signature=signature;$('setup-missing').replaceChildren(...missing.map(issue=>{const li=document.createElement('li');li.append(document.createTextNode(issue.text));const fix=document.createElement('button');fix.type='button';fix.className='secondary';fix.textContent='Review step '+(issue.step+1);fix.onclick=()=>showStep(issue.step);li.append(fix);return li}));}
 $('setup-review-summary').textContent=missing.length?'Setup is not finished. Complete the items below to unlock the manager.':'✓ All required settings are saved. Finish first-time configuration to unlock the manager.';
 $('setup-review-summary').classList.toggle('setup-ok',!missing.length);
}
function showStep(value){step=Math.max(0,Math.min(3,value));history.replaceState(null,'',hashes[step]);renderWizard();$('setup-step-title').focus();$('setup-checklist').scrollIntoView({block:'start',behavior:'smooth'});}
window.addEventListener('configuration-loaded',()=>{baseline=snapshot();renderWizard()});
window.addEventListener('configuration-saved',event=>{baseline=event.detail;refresh()});
document.addEventListener('input',renderWizard);document.addEventListener('change',renderWizard);
document.addEventListener('invalid',event=>{if(state?.setup_required){const panel=event.target.closest('[data-setup-step]');if(panel)showStep(Number(panel.dataset.setupStep))}},true);
document.addEventListener('DOMContentLoaded',()=>{
 $('first-run').dataset.setupStep='0';
 document.querySelectorAll('[data-setup-go]').forEach(el=>el.onclick=()=>showStep(Number(el.dataset.setupGo)));
 $('setup-back').onclick=()=>showStep(step-1);
 $('setup-next').onclick=async()=>{if(step===2){const saved=await $('save').onclick();if(!saved)return;await refresh()}showStep(step+1)};
 $('setup-finish').onclick=async()=>{if(issues().length||finishing)return;finishing=true;renderWizard();$('setup-finish-status').textContent='Verifying settings…';try{await call('complete',{});location.href='/#artists'}catch(e){$('setup-finish-status').textContent=e.message;await refresh()}finally{finishing=false;renderWizard()}};
 renderWizard();
});

async function refresh(){try{state=await call('');reachable=true;window.dispatchEvent(new CustomEvent('initial-setup-state',{detail:state}));$('setup-toc').value||=state.source;$('setup-source-status').textContent=state.creators?`${state.creators} creators imported`:'';window.dispatchEvent(new CustomEvent('setup-state',{detail:state}));$('setup-share-path').value||=state.share?.path||'';$('setup-share-user').value||=state.share?.username||'';$('setup-share-domain').value||=state.share?.domain||'';const labels={phone:'Phone number, including country code',code:'Telegram login code',password:'Telegram two-step verification password'};const prompt=labels[state.login];$('setup-answer-form').hidden=!prompt;$('setup-login').disabled=busy||!!prompt||state.login==='connecting';$('setup-login-status').textContent=state.login==='connected'?'✓ Telegram account connected':state.login==='failed'?'Sign-in failed or timed out. Please try again.':state.login==='connecting'?'Connecting to Telegram…':'';if(prompt){$('setup-answer-label').textContent=prompt;$('setup-answer').type=state.login==='phone'?'tel':'password'}renderWizard()}catch(e){reachable=false;renderWizard();message(e.message,true)}}
$('setup-login').onclick=async()=>{busy=true;try{await call('login',{});message('Follow the sign-in prompts below.')}catch(e){message(e.message,true)}finally{busy=false;refresh()}};
$('setup-answer-form').onsubmit=async e=>{e.preventDefault();const value=$('setup-answer').value;$('setup-answer').value='';try{await call('answer',{value,challenge:state.challenge})}catch(e){message(e.message,true)}refresh()};
$('setup-source-form').onsubmit=async e=>{e.preventDefault();$('setup-import').disabled=true;message('Reading the Table of Contents…');try{const d=await call('source',{url:$('setup-toc').value});message(`Imported ${d.creators} creators. You can now select artists.`)}catch(e){message(e.message,true)}finally{$('setup-import').disabled=false;refresh()}};
document.addEventListener('DOMContentLoaded',()=>{$('setup-share-form').onsubmit=async e=>{
 e.preventDefault();const button=$('setup-share-connect'),password=$('setup-share-password'),status=$('setup-share-message');
 const report=(text,error=false)=>{status.textContent=text;status.style.color=error?'#a12632':''};
 button.disabled=true;report('Connecting to the network share…');
 try{await call('share',{path:$('setup-share-path').value,username:$('setup-share-user').value,password:password.value,domain:$('setup-share-domain').value});password.value='';report('Connected. Download location saved. Reloading settings…');location.reload()}
 catch(e){report(e.message,true);status.scrollIntoView({block:'nearest',behavior:'smooth'})}
 finally{button.disabled=false}
};});

refresh();setInterval(refresh,2000);
})();
