// Public documentation screenshots: real UI, synthetic data, no account writes.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const fs=require('node:fs');const path=require('node:path');
const root=path.resolve(__dirname,'..'),out=path.join(root,'assets/screenshots');fs.mkdirSync(out,{recursive:true});
const now=Date.now()/1000;
const names=['Northstar Miniatures','Oak & Iron Studio','Example Atelier'];
const creators=names.map((name,i)=>({id:i+1,name,url:'https://example.invalid/artist/'+i}));
const subscriptions=creators.map(c=>({id:c.id,name:c.name,folder:c.name,start_month:'2026-09'}));
const items=creators.flatMap((c,i)=>[0,1].map(n=>({key:'file'+i+n,creator_id:c.id,creator:c.name,folder:c.name,object_id:i*10+n,archive_id:i*10+n,object_name:n?'Supported models':'Main collection',filename:n?'Supported models.zip':'Main collection.zip',release:'November 2026',release_name:c.name+' 2026-11',release_key:String(i+1).repeat(64),release_month:'2026-11',release_folder:c.name+' 2026-11',size:220000000+n*140000000,completed:i<2,images_checked:i<2})));
const mmf={connected:true,active:false,resumable:false,creators,folders:names,settings:{revision:1,subscriptions},items,counts:{available:2},checked_at:now,next_check_at:now+3600,interval_hours:4,job:{},preparations:{},collages:Object.fromEntries(creators.slice(0,2).map((c,i)=>[String(i+1).repeat(64),{ready:true,valid:i===0,exists:i===0,folder:c.name,release:c.name+' 2026-11'}]))};
const transfers=[{id:1,creator:names[0],filename:'Northstar 2026-11 - Skyward Citadel.7z.001',phase:'downloading',bytes:1500000000,total:2200000000,speed_bps:18000000,dc_id:2},{id:2,creator:names[1],filename:'Oak and Iron 2026-11 - Forest Guardians.zip',phase:'downloading',bytes:340000000,total:780000000,speed_bps:9500000,dc_id:4}];
const queue={active:true,can_start:false,can_resume:false,subscriptions_saved:3,worker_state:'downloading',total_files:5,completed_files:2,history_completed:128,bytes_downloaded:2840000000,bytes_total:4300000000,progress_total:4300000000,run:{id:'demo',started_at:new Date(Date.now()-125000).toISOString(),message:'Downloading November releases',warnings:[]},scheduler:{transfers},bandwidth:{active_files:2,speed_bps:27500000,target_mbps:28},files:transfers.map(t=>({...t,state:'downloading',bytes_total:t.total,bytes_downloaded:t.bytes})),recent_completed:[{creator:names[2],filename:'Example Atelier 2026-11 - Autumn Collection.zip',bytes_total:1000000000,download_finished_at:'demo',download_seconds:42,download_average_bps:24000000,image_count:8}]};
(async()=>{const browser=await chromium.launch({headless:true});try{const page=await browser.newPage({viewport:{width:1440,height:1040},deviceScaleFactor:1});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{const url=new URL(route.request().url());let data={};const p=url.pathname;
 if(p==='/api/artist-profiles/image'){const letter=(url.searchParams.get('name')||'A')[0].replace(/[^A-Z]/g,'A');await route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" rx="12" fill="#7651a6"/><text x="32" y="43" text-anchor="middle" font-family="sans-serif" font-size="34" fill="white">${letter}</text></svg>`});return;}
 if(p==='/api/setup')data={setup_required:false,consent_required:false,login:'connected',account:{name:'Demo account'},source:'https://t.me/c/123456789/100',creators:3};
 else if(p==='/api/config')data={revision:1,download_directory:'/downloads',download_storage:'local',available:true,folders:names,telegram_servers:{},download_servers:{},mmf_beta_enabled:true,release_volume_mib:4000};
 else if(p==='/api/subscriptions')data={revision:1,subscriptions:[]};
 else if(p==='/api/queue')data=queue;
 else if(p==='/api/availability')data={files:2,creators:2,releases:2,subscribed:3,checked_at:now,next_check_at:now+3600,interval_seconds:14400};
 else if(p==='/api/mmf'||p==='/api/mmf/status')data=mmf;
 else if(p==='/api/mmf/creators')data={creators};
 else if(p==='/api/mmf/folders')data={folders:names};
 else if(p==='/api/mmf/progress')data={active:false,job:{}};
 else if(p==='/api/updates')data={available:false,current:fs.readFileSync(path.join(root,'VERSION'),'utf8').trim(),last_checked:now,repository:'bora77/telegram-stl-manager',message:'You have the latest version.'};
 else if(p==='/api/auto-download')data={telegram:{enabled:false},mmf:{enabled:false}};
 await route.fulfill({json:data});});
 async function capture(url,file){await page.goto('http://127.0.0.1:6093'+url);await page.waitForTimeout(600);if(url==='/mmf'){await page.locator('.release-row').nth(1).locator('summary').first().click();await page.setViewportSize({width:1440,height:1200})}else await page.setViewportSize({width:1440,height:1040});await page.evaluate(()=>{const badge=document.createElement('div');badge.textContent='DEMO DATA';Object.assign(badge.style,{position:'fixed',right:'24px',bottom:'16px',zIndex:'999',padding:'6px 10px',borderRadius:'7px',background:'#272332',color:'white',font:'12px system-ui'});document.body.append(badge)});await page.screenshot({path:path.join(out,file)});}
 await capture('/','download-queue.png');
 await capture('/mmf','release-workflow.png');
 await capture('/mmf#artists','artist-subscriptions.png');
 if(errors.length)throw Error(errors.join('\n'));
 console.log('Captured three screenshots using synthetic data only.');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
