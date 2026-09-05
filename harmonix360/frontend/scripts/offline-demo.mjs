/**
 * Phase 8 offline demo scenario, end to end in a real browser.
 *
 *   npm i -D playwright && npx playwright install chromium   # once
 *   node scripts/offline-demo.mjs
 *
 * Playwright is NOT a project dependency: this drives the built app in a real
 * browser and is run by hand before a demo, not in the unit-test loop. The
 * backend equivalents live in harmonix360/backend/scripts/.
 *
 * PRD §7 advanced metric: "Offline attendance/leave sync produces zero
 * duplicate records across a kill-network -> mutate -> reconnect cycle."
 *
 * The network is cut at the BROWSER (Playwright offline mode), so the app
 * takes the same path a phone in a basement would: navigator.onLine false,
 * /health unreachable, writes landing in IndexedDB, and the reconnect handler
 * flushing the outbox. Row counts are then read back from the API, so a
 * duplicate shows up as a ROW rather than as a missing banner.
 *
 * It requires the stack running (docker compose up -d) and a seeded database
 * whose employee login is linked to an Employee row - which app/seed.py does.
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const BASE = process.env.UI_BASE ?? 'http://localhost:3000';
const API = process.env.API_BASE ?? 'http://localhost:8000';
const SHOTS = './shots-offline';
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
function record(label, ok, detail = '') {
  results.push({ label, ok, detail });
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ' — ' + detail : ''}`);
}
const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });

async function apiToken(email, password) {
  const r = await fetch(`${API}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!r.ok) throw new Error(`login ${email}: ${r.status}`);
  return (await r.json()).access_token;
}

async function ownAttendanceCount(token) {
  const r = await fetch(`${API}/api/v1/attendance/me?limit=200`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!r.ok) throw new Error(`attendance/me: ${r.status}`);
  return (await r.json()).total;
}

async function ownRequestCount(token) {
  const r = await fetch(`${API}/api/v1/time-off-requests/me?limit=200`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!r.ok) throw new Error(`time-off-requests/me: ${r.status}`);
  return (await r.json()).total;
}

const run = async () => {
  const employeeToken = await apiToken('employee@peoplepay360.com', 'employee123');

  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));

  console.log('\n=== Setup — log in as the Employee, online ===');
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
  await page.getByLabel(/email/i).fill('employee@peoplepay360.com');
  await page.getByLabel(/password/i).fill('employee123');
  await page.getByRole('button', { name: /sign in|log in/i }).click();
  await page.waitForSelector('nav[aria-label="Main"] a', { timeout: 20000 });
  record('Employee signed in', true);

  // Visit Time Off while still ONLINE. The request form's leave-type list is a
  // network read (`/time-off-types/lookup`), and nothing caches it in
  // IndexedDB — `time_off_type` is deliberately NOT a registered sync entity,
  // because Phase 8 registers exactly two. So an offline submission is only
  // possible if the person had the screen open before losing signal, which is
  // the realistic case and is what this reproduces. See the limitation note in
  // docs/offline-sync-conflicts.md.
  await page.goto(`${BASE}/time-off`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  await page.goto(`${BASE}/attendance`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);

  const attendanceBefore = await ownAttendanceCount(employeeToken);
  const requestsBefore = await ownRequestCount(employeeToken);
  console.log(`  server rows before: attendance=${attendanceBefore}, requests=${requestsBefore}`);

  // ------------------------------------------------------ KILL THE NETWORK
  console.log('\n=== Kill network ===');
  await context.setOffline(true);
  // reachability polls /health; give its subscribers a moment to notice.
  await page.waitForSelector('[data-testid="offline-banner"]', { timeout: 30000 });
  record('OfflineBanner appears once the app really cannot reach the server', true);
  await shot(page, '1-offline-banner');

  // ------------------------------------------------------- MUTATE OFFLINE
  console.log('\n=== Mutate while offline ===');
  await page.getByRole('button', { name: /check in now/i }).click();
  await page.waitForSelector('[data-testid="attendance-pending-sync"]', { timeout: 20000 });
  const pendingText = await page.locator('[data-testid="attendance-pending-sync"]').innerText();
  record(
    'Check-in is accepted offline and shown as waiting to sync',
    /waiting to sync/i.test(pendingText),
    pendingText.split('\n')[0],
  );
  await shot(page, '2-checkin-queued-offline');

  // In a throwaway tab: a failed navigation leaves the page on a browser
  // error page, and the tab under test still has an outbox entry to watch.
  const scratchTab = await context.newPage();
  const hardLoadFails = await scratchTab
    .goto(`${BASE}/attendance`, { waitUntil: 'domcontentloaded', timeout: 8000 })
    .then(() => false)
    .catch(() => true);
  await scratchTab.close();
  record(
    'A hard page load while offline fails (no service worker) — known limit, not a regression',
    hardLoadFails,
    'in-app navigation still works; the app shell is not cached',
  );

  const midFlight = await ownAttendanceCount(employeeToken);
  record(
    'Nothing reached the server while offline',
    midFlight === attendanceBefore,
    `server still ${midFlight}`,
  );

  // A leave request, queued offline as well. Navigated by CLICKING the nav
  // link, not by page.goto: a full page load while offline needs a service
  // worker to serve the app shell, and this build deliberately has none (see
  // docs/offline-sync-conflicts.md). A user who is already inside the app
  // moves between screens client-side, which is what this reproduces.
  await page.getByRole('link', { name: /Time Off/ }).click();
  await page.waitForTimeout(2000);
  const newRequest = page.getByRole('button', { name: /new request|new time off|^new/i }).first();
  let queuedRequest = false;
  if (await newRequest.count()) {
    await newRequest.click();
    await page.waitForTimeout(1200);
    const dialog = page.locator('div.fixed.inset-0.z-50').last();
    const selects = dialog.locator('select');
    for (let i = 0; i < (await selects.count()); i++) {
      const opts = await selects.nth(i).locator('option').allTextContents();
      const real = opts.find((o) => o && !/select/i.test(o));
      if (real) await selects.nth(i).selectOption({ label: real });
    }
    const from = dialog.locator('input[type="date"]').first();
    const to = dialog.locator('input[type="date"]').last();
    if (await from.count()) await from.fill('2026-12-07');
    if (await to.count()) await to.fill('2026-12-09');
    const save = dialog.getByRole('button', { name: /save|submit|create/i }).last();
    if (await save.count()) {
      await save.click();
      await page.waitForTimeout(1500);
      queuedRequest = (await page.locator('[data-testid="time-off-pending-sync"]').count()) > 0;
    }
  }
  record('Leave request queued offline', queuedRequest, queuedRequest ? '' : 'form not reachable offline');
  if (queuedRequest) await shot(page, '3-request-queued-offline');

  // Close the dialog if it is still up: its backdrop covers the whole viewport
  // and would swallow every later click.
  for (let i = 0; i < 3; i++) {
    const openDialog = page.locator('div.fixed.inset-0.z-50');
    if ((await openDialog.count()) === 0) break;
    const close = openDialog.last().getByRole('button', { name: /close|cancel/i }).first();
    if (await close.count()) await close.click().catch(() => {});
    else await page.keyboard.press('Escape');
    await page.waitForTimeout(600);
  }

  // ---------------------------------------------------------- RECONNECT
  console.log('\n=== Reconnect ===');
  await context.setOffline(false);
  // The reachability subscriber calls runSync() on the transition; wait for
  // the banner to clear and the queue to drain rather than sleeping blindly.
  await page.waitForSelector('[data-testid="offline-banner"]', { state: 'detached', timeout: 60000 });
  record('OfflineBanner clears on reconnect', true);

  await page.getByRole('link', { name: /Attendance/ }).click();
  await page.waitForTimeout(2000);
  for (let i = 0; i < 30; i++) {
    if ((await page.locator('[data-testid="attendance-pending-sync"]').count()) === 0) break;
    await page.waitForTimeout(1000);
    await page.reload({ waitUntil: 'networkidle' });
  }
  const stillPending = await page.locator('[data-testid="attendance-pending-sync"]').count();
  record('Outbox drained after reconnect', stillPending === 0);
  await shot(page, '4-after-reconnect');

  const attendanceAfter = await ownAttendanceCount(employeeToken);
  const requestsAfter = await ownRequestCount(employeeToken);
  record(
    'Exactly one attendance row reached the server — no duplicate',
    attendanceAfter === attendanceBefore + 1,
    `${attendanceBefore} -> ${attendanceAfter}`,
  );
  if (queuedRequest) {
    record(
      'Exactly one time-off request reached the server — no duplicate',
      requestsAfter === requestsBefore + 1,
      `${requestsBefore} -> ${requestsAfter}`,
    );
  }

  // --------------------------------------------- a second sync is a no-op
  console.log('\n=== Re-sync (the retry a flaky network causes) ===');
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(3000);
  const afterResync = await ownAttendanceCount(employeeToken);
  record(
    'Re-running sync creates nothing further',
    afterResync === attendanceAfter,
    `still ${afterResync}`,
  );

  await browser.close();

  console.log('\n=== Summary ===');
  const failed = results.filter((r) => !r.ok);
  console.log(`  ${results.length - failed.length}/${results.length} offline checks passed`);
  console.log(`  page errors: ${pageErrors.length}`);
  if (pageErrors.length) console.log('  ' + pageErrors.slice(0, 4).join('\n  '));
  if (failed.length) for (const f of failed) console.log(`   FAILED: ${f.label} ${f.detail}`);
  process.exit(failed.length ? 1 : 0);
};

run().catch((e) => {
  console.error('offline demo crashed:', e);
  process.exit(2);
});
