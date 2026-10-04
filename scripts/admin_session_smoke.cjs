const { chromium } = require('playwright');
const assert = require('node:assert/strict');
if (process.env.EXITCODE_E2E !== '1') throw new Error('Use EXITCODE_E2E=1 only on an isolated QA database; this resets the event.');
const base = process.env.TEST_URL || 'http://127.0.0.1:5057';
(async () => {
  const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  const context = await browser.newContext();
  const admin = await context.newPage(), participant = await context.newPage();
  const errors = [];
  for (const page of [admin, participant]) page.on('pageerror', error => errors.push(error.message));
  async function adminStillActive() {
    await admin.bringToFront();
    const response = await admin.request.get(base + '/api/admin/dashboard-data', { maxRedirects: 0 });
    assert.equal(response.status(), 200, await response.text());
    await admin.locator('#admin-workspace').waitFor();
    assert.match(admin.url(), /\/admin\/dashboard/);
    assert.equal(await admin.locator('#participant-gate').count(), 0);
  }
  try {
    await admin.request.post(base + '/admin/login', { form: { username: 'admin', password: 'exitcode0_admin_2026' } });
    assert.equal((await admin.request.post(base + '/api/admin/reset-event', { data: { confirmation: 'RESET EVENT' } })).status(), 200);
    await admin.goto(base + '/admin/dashboard');
    await participant.request.post(base + '/register', { form: { name: 'Shared browser session QA', member1: 'Ada', member2: 'Grace' } });
    await participant.goto(base + '/waiting');
    await participant.bringToFront();
    assert.equal(await participant.locator('#participant-gate').isVisible(), false);
    await adminStillActive();
    for (const panel of ['teams', 'feedback', 'overview']) {
      await admin.locator(`.admin-nav a[data-panel="${panel}"]`).click();
      await participant.bringToFront();
      await participant.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
      await adminStillActive();
      await admin.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    }
    console.log('PASS organizer survives same-browser tab switching, visibility refreshes, and participant registration');
    await participant.goto(base + '/logout');
    await adminStillActive();
    await participant.request.post(base + '/register', { form: { action: 'login', team_lookup: 'Shared browser session QA' } });
    await participant.goto(base + '/waiting');
    await adminStillActive();
    await admin.reload();
    await adminStillActive();
    const cookies = await context.cookies();
    assert.ok(cookies.some(cookie => cookie.name === 'session'));
    assert.ok(cookies.some(cookie => cookie.name === 'exitcode_admin_session' && cookie.httpOnly));
    console.log('PASS participant sign-out/sign-in and organizer refresh preserve separate authentication cookies');
    await admin.goto(base + '/admin/logout');
    assert.equal((await admin.request.get(base + '/api/admin/dashboard-data', { maxRedirects: 0 })).status(), 302);
    assert.equal((await participant.request.get(base + '/api/team-progress')).status(), 200);
    assert.deepEqual(errors, []);
    console.log('PASS explicit organizer sign-out revokes admin access while preserving participant login; no browser errors');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
