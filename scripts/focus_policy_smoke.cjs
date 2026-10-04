const {chromium}=require('playwright');
const assert=require('node:assert/strict');
if (process.env.EXITCODE_E2E !== '1') throw new Error('This test resets its target event; use an isolated test database.');
const base=process.env.TEST_URL || 'http://127.0.0.1:5057';
(async()=>{
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
 const admin=await browser.newContext();const user=await browser.newContext({viewport:{width:1440,height:1000}});const page=await user.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const enter=async()=>{await page.bringToFront();await page.locator('#participant-enter').click();await page.waitForFunction(()=>window.ParticipantGuard?.active);};
 const state=async()=> (await user.request.get(base+'/api/fullscreen/status')).json();
 try{
 await admin.request.post(base+'/admin/login',{form:{username:'admin',password:'exitcode0_admin_2026'}});
 await admin.request.post(base+'/api/admin/reset-event',{data:{confirmation:'RESET EVENT'}});
 await user.request.post(base+'/register',{form:{name:'Focus QA',member1:'A',member2:'B'}});
 await page.goto(base+'/waiting');assert.equal(await page.locator('#participant-gate').isVisible(),false);
 await page.evaluate(()=>window.dispatchEvent(new Event('blur')));
 assert.equal((await state()).violations,0);
 await admin.request.post(base+'/api/admin/event-action',{data:{action:'start'}});
 await page.waitForURL(/arena/);await enter();
 // Exercise a window-blur departure while the DOM remains fullscreen (desktop app-switch path).
 await page.evaluate(()=>window.dispatchEvent(new Event('blur')));
 await page.waitForFunction(()=>document.getElementById('participant-violations').textContent.startsWith('1 /'));
 assert.equal((await state()).violations,1);assert.equal(await page.evaluate(()=>!!document.fullscreenElement),true);
 await page.evaluate(()=>{window.dispatchEvent(new Event('blur'));document.dispatchEvent(new Event('fullscreenchange'));});
 assert.equal((await state()).violations,1);assert.equal((await state()).is_fullscreen,false);
 console.log('PASS window blur while still fullscreen warns once and closes server access');
 await enter();
 // Explicit visibility-event coverage avoids OS/window-manager differences in automated Chrome.
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));});
 await page.waitForFunction(()=>document.getElementById('participant-gate-title').textContent==='Account blocked', null, {polling:100});
 assert.equal((await user.request.get(base+'/api/team-progress')).status(),403);
 console.log('PASS simulated tab-hidden departure after reentry blocks the account');
 assert.deepEqual(errors,[]);
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
