const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true});try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const state={current:'0.2.5',latest:'0.2.8',available:true,platform:'docker',repository:'example/project',last_checked:Date.now()/1000,changes:[{version:'0.2.8',body:'Latest changes'},{version:'0.2.7',body:'<img src=x onerror=alert(1)> Earlier changes'},{version:'0.2.6',body:'Privacy improvements'}]};
 await page.route('**/api/**',route=>{const p=new URL(route.request().url()).pathname;return route.fulfill({json:p==='/api/updates'?state:p==='/api/setup'?{setup_required:false,consent_required:false,login:'idle'}:p==='/api/config'?{download_directory:'/downloads',folders:[],available:true,telegram_servers:{},download_servers:{}}:{counts:{},settings:{subscriptions:[]}}})});
 await page.goto('http://127.0.0.1:6093/config');await page.locator('#release-update-dialog').waitFor();
 assert((await page.locator('#release-update-summary').innerText()).includes('0.2.5'));
 assert.equal(await page.locator('#release-update-changes section').count(),3);
 assert.equal(await page.locator('#release-update-changes img').count(),0);
 assert((await page.locator('#release-update-route').innerText()).includes('Update.command'));
 await page.locator('#release-update-close').click();await page.reload();await page.waitForTimeout(300);
 assert(!(await page.locator('#release-update-dialog').isVisible()));
 await page.locator('#footer-update').click();assert(await page.locator('#release-update-dialog').isVisible());
 await page.locator('#release-update-later').click();await page.reload();await page.waitForTimeout(300);assert(!(await page.locator('#release-update-dialog').isVisible()));
 await page.evaluate(()=>localStorage.clear());await page.reload();await page.setViewportSize({width:390,height:844});await page.locator('#release-update-dialog').waitFor();
 assert(await page.locator('#release-update-dialog').evaluate(e=>e.getBoundingClientRect().width<=innerWidth));assert.deepEqual(errors,[]);
 console.log('PASS: all intervening notes, safe text, once-per-version, reminder, manual reopen and mobile dialog.');
}finally{await browser.close()}})().catch(error=>{console.error(error);process.exit(1)});
