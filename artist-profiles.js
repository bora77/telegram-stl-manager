(() => {
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n};
  const url=name=>'/api/artist-profiles/image?name='+encodeURIComponent(name)+'&library=__ARTIST_LIBRARY_REVISION__';
  const cache=new Map();let current='',generation=0;
  async function info(name){
    if(!cache.has(name))cache.set(name,fetch('/api/artist-profiles?name='+encodeURIComponent(name)).then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.error);return d}));
    return cache.get(name);
  }
  function links(data){
    const box=el('span');box.className='artist-platform-links';
    for(const [label,href] of Object.entries(data.links||{})){
      if(!href.startsWith('https://'))continue;
      const a=el('a',label);a.href=href;a.target='_blank';a.rel='noopener noreferrer';if(data.link_sources?.[label])a.title='Collected from '+data.link_sources[label];box.append(a);
    }
    return box;
  }
  const dialog=el('dialog');dialog.className='artist-profile-dialog';
  const heading=el('h2'),image=el('img');image.className='artist-profile-preview';
  const detail=el('p'),destinations=el('div'),status=el('p');status.setAttribute('role','status');
  const input=el('input');input.type='file';input.accept='image/png,image/jpeg,image/webp';input.hidden=true;
  const buttons=el('div');buttons.className='artist-profile-actions';
  const upload=el('button','Upload replacement'),initials=el('button','Use initials'),restore=el('button','Restore collected logo'),close=el('button','Close');
  buttons.append(upload,initials,restore,close);dialog.append(heading,image,detail,destinations,input,buttons,status);
  document.body.append(dialog);
  async function show(name){
    current=name;const token=++generation;heading.textContent=name;image.src=url(name)+'&v='+Date.now();image.alt=name+' logo';
    detail.textContent='Loading profile…';destinations.replaceChildren();status.textContent='';restore.disabled=true;
    if(!dialog.open)dialog.showModal();
    try{const d=await info(name);if(token!==generation)return;
      dialog.dataset.mode=d.mode;
      detail.textContent=d.mode==='upload'?'Your uploaded logo.':d.mode==='initials'?'Initials selected.':d.has_default?'Collected creator profile image.':'No reliably matched image collected; showing initials.';
      destinations.replaceChildren(links(d));restore.disabled=d.mode==='default';
      if(d.source){const source=el('a','Image source');source.href=d.source;source.target='_blank';source.rel='noopener noreferrer';destinations.append(source)}
    }catch(e){if(token===generation){detail.textContent='Profile unavailable.';status.textContent=e.message}}
  }
  async function save(mode,data){
    const name=current;status.textContent='Saving…';buttons.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{const r=await fetch('/api/artist-profiles',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,mode,image:data})});const d=await r.json();if(!r.ok)throw Error(d.error);
      cache.delete(name);document.querySelectorAll('img[data-artist-logo]').forEach(img=>{if(img.dataset.artistLogo===name)img.src=url(name)+'&v='+Date.now()});
      if(current===name){await show(name);status.textContent='Saved.'}
    }catch(e){status.textContent=e.message}
    finally{buttons.querySelectorAll('button').forEach(b=>b.disabled=false);restore.disabled=dialog.dataset.mode==='default';input.value=''}
  }
  upload.onclick=()=>input.click();initials.onclick=()=>save('initials');restore.onclick=()=>save('default');close.onclick=()=>dialog.close();
  input.onchange=async()=>{const file=input.files[0];if(!file)return;if(file.size>5000000){status.textContent='Choose an image smaller than 5 MB.';return}
    const reader=new FileReader();reader.onload=()=>save('upload',String(reader.result).split(',')[1]);reader.readAsDataURL(file);
  };
  window.ArtistProfiles={badge(name,{compact=false,withLinks=false}={}){
    const wrap=el('span');wrap.className='artist-profile-badge'+(compact?' compact':'');
    const button=el('button');button.type='button';button.className='artist-logo-button';button.title='Logo and creator links: '+name;button.setAttribute('aria-label',button.title);
    const img=el('img');img.src=url(name);img.alt='';img.loading='lazy';img.dataset.artistLogo=name;button.append(img);button.onclick=()=>show(name);wrap.append(button);
    if(withLinks)info(name).then(d=>wrap.append(links(d))).catch(()=>{});
    return wrap;
  },show};
})();
