// Real-DOM verification harness for the Synapse web UI (P4, adapted from
// amended SPEC-WEB D5 + full "Registre" redesign): logs in by
// ORGANIZATION SELECTION (POST /api/login, no more password), loads
// the app with the session, then checks the rendering of the 8 views, the
// live org chart, the Conversations view (list/detail/composer), keyboard
// focus and the absence of console errors. The "Registre" shell (brandbar +
// tabstrip) replaces the v2 sidebar: the shell selectors were adapted
// (`.sidebar-footer` → `.session-identity`, `#app .sidebar` → `#app .brandbar`).
// The selection-based login screen is verified by verify_login.mjs.
import { createRequire } from 'module';
const require = createRequire(import.meta.url);  // node_modules local to the harness
const { JSDOM } = require('jsdom');

const ORIGIN = process.env.SYNAPSE_WEB_ORIGIN || 'http://127.0.0.1:8092';
const ORG = process.env.SYNAPSE_WEB_ORG || '';

if (!ORG) {
  console.error('HARNESS: SYNAPSE_WEB_ORG required (organization to select).');
  process.exit(1);
}

const realFetch = globalThis.fetch;

(async () => {
  // 1) Selection-based login: POST /api/login -> session cookie.
  const loginRes = await realFetch(`${ORIGIN}/api/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ organization_name: ORG }),
  });
  if (loginRes.status !== 200) {
    console.error('HARNESS: selection-based login refused (HTTP ' + loginRes.status + ').');
    process.exit(1);
  }
  const setCookie = loginRes.headers.get('set-cookie') || '';
  const sessionCookie = (setCookie.split(';')[0] || '').trim();

  const html = await (await realFetch(`${ORIGIN}/`)).text();
  const dom = new JSDOM(html, {
    url: ORIGIN + '/',
    runScripts: 'outside-only',
    pretendToBeVisual: true,
  });
  const { window } = dom;
  const errors = [];
  window.addEventListener('error', e => errors.push('window.onerror: ' + e.message));
  const origConsole = window.console;
  window.console = {
    ...origConsole,
    error: (...a) => { errors.push('console.error: ' + a.join(' ')); origConsole.error(...a); },
  };

  // jsdom does not implement matchMedia: minimal shim (desktop, no fallback).
  window.matchMedia = window.matchMedia || (q => ({
    matches: false, media: q, onchange: null,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
    dispatchEvent() { return false; },
  }));
  // Real fetch to the backend with the session cookie, like api.js.
  window.fetch = async (url, opts = {}) => {
    const headers = { ...(opts.headers || {}) };
    if (sessionCookie) headers.Cookie = sessionCookie;
    const res = await realFetch(ORIGIN + url, { ...opts, headers });
    const text = await res.text();
    return {
      status: res.status,
      ok: res.ok,
      headers: { get: n => res.headers.get(n) },
      json: async () => JSON.parse(text),
      text: async () => text,
    };
  };

  // Globals used by the app modules.
  globalThis.window = window;
  globalThis.document = window.document;
  globalThis.location = window.location;
  globalThis.localStorage = window.localStorage;
  globalThis.fetch = window.fetch;  // the modules call `fetch` (free var)
  Object.defineProperty(globalThis, 'navigator', { value: window.navigator, configurable: true });

  const app = await import(new URL('../../synapse/webui/js/app.js', import.meta.url));
  const { api } = await import(new URL('../../synapse/webui/js/api.js', import.meta.url));
  await new Promise(r => setTimeout(r, 2000)); // boot + first render + snapshot

  const doc = window.document;
  const q = s => doc.querySelector(s);
  const qa = s => Array.from(doc.querySelectorAll(s));

  const report = {};
  report.sessionIdentity = q('.session-identity')?.textContent?.trim() || null;
  report.appMounted = !!q('#app .brandbar');
  report.appVisible = q('#app') ? !q('#app').hidden : false;  // the shell is displayed
  // The shortcut hints "g d / g a / g c" were removed from the
  // menu (user request): no .nav-shortcut is rendered anymore.
  report.noNavShortcuts = qa('.nav-shortcut').length === 0;
  // Regression guard (real bug 2026-08-06): after login, the login
  // window must be hidden — the explicit [hidden] CSS is checked by
  // verify_login.mjs.
  report.loginHiddenAfterAuth = q('#login-root') ? q('#login-root').hidden : null;

  // jsdom does not always trigger hashchange on location.hash: nav()
  // forces the event (environment quirk, not an app issue).
  const nav = route => {
    window.location.hash = route;
    window.dispatchEvent(new window.Event('hashchange'));
  };

  // Organization view (live org chart).
  nav('#/organization');
  await new Promise(r => setTimeout(r, 1200));

  report.orgHeader = q('.org-hero .org-name')?.textContent || null;
  report.segmentedButtons = qa('.seg-btn').map(b => b.textContent.trim());
  report.orgChartExists = !!q('.org-chart');
  report.orgChartColumns = qa('.org-chart-col').map(c => c.getAttribute('aria-label'));
  report.orgChartNodes = qa('.org-chart-node').length;
  report.orgChartLinks = qa('.org-chart-link').length;

  // Toggle to cards, then back.
  const cardsBtn = qa('.seg-btn').find(b => b.textContent.trim() === 'Cards');
  if (cardsBtn) { cardsBtn.click(); await new Promise(r => setTimeout(r, 400)); }
  report.cardsViewAfterToggle = !!q('.dept-card');
  const chartBtn = qa('.seg-btn').find(b => b.textContent.trim() === 'Org chart');
  if (chartBtn) { chartBtn.click(); await new Promise(r => setTimeout(r, 400)); }
  report.chartViewAfterToggleBack = !!q('.org-chart');

  // Keyboard: focus on an org-chart agent link.
  const firstLink = q('.org-chart-link');
  if (firstLink) { firstLink.focus(); report.focusIsChartLink = doc.activeElement === firstLink; }

  // Conversations view: switch [Agent ↔ Agent | Humain ↔ Agent] + messaging.
  nav('#/conversations');
  const waitFor = async (fn, timeoutMs = 6000, step = 150) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (fn()) return true;
      await new Promise(r => setTimeout(r, step));
    }
    return false;
  };
  await waitFor(() => qa('.conv-row').length > 0);
  await new Promise(r => setTimeout(r, 400));  // final render stabilized
  report.convSwitch = qa('.conv-mode-switch .seg-btn').map(b => b.textContent.trim());
  report.convDefaultMode = q('.conv-mode-switch .seg-btn.is-active')?.textContent?.trim() || null;
  report.conversationsList = qa('.conv-row').length;
  report.conversationsError = q('.conv-layout .banner-danger')?.textContent?.trim() || null;

  // Agent ↔ Agent (read-only): detail WITHOUT composer, alternation of
  // the two interlocutors (bubbles on the left AND right, same style as HA).
  // We open the RICHEST conversation (two active interlocutors) —
  // a single-message conversation cannot show alternation.
  let bestRow = null;
  let bestCount = -1;
  for (const row of qa('.conv-row')) {
    const m = (row.textContent.match(/(\d+) message/) || [])[1];
    const n = m ? parseInt(m, 10) : 0;
    if (n > bestCount) { bestCount = n; bestRow = row; }
  }
  if (bestRow) { bestRow.click(); await waitFor(() => !!q('.conv-thread')); }
  report.aaThread = !!q('.conv-thread');
  report.aaComposerAbsent = !q('.conv-input textarea, textarea[placeholder*="Reply"]');
  report.aaBothSides = qa('.conv-thread .msg-row.mine').length > 0
    && qa('.conv-thread .msg-row.theirs').length > 0;
  report.aaHeadShowsBoth = (q('.conv-detail-name')?.textContent || '').includes('↔');

  // SMOOTH refresh: a refresh (snapshot emission) must neither
  // rebuild the thread nor re-show "Loading content…".
  const threadBefore = q('.conv-thread');
  api._emit();
  await new Promise(r => setTimeout(r, 900));
  report.threadStableAfterRefresh = q('.conv-thread') === threadBefore;
  report.noReloadSpinner = !q('.conv-detail-loading');

  // Humain ↔ Agent: dedicated list + composer.
  const haBtn = qa('.conv-mode-switch .seg-btn').find(b => b.textContent.includes('Human'));
  if (haBtn) {
    haBtn.click();
    // switchMode is async (re-render via hashchange): we wait for the
    // MODE to actually switch, not for the thread (still present from the AA view).
    await waitFor(() => (q('.conv-mode-switch .seg-btn.is-active')?.textContent?.trim() || '') === 'Humain ↔ Agent');
  }
  report.haActive = q('.conv-mode-switch .seg-btn.is-active')?.textContent?.trim() || null;
  await waitFor(() => qa('.conv-row').length > 0);
  report.haList = qa('.conv-row').length;
  // Unread handling: the received message (agent -> human) is marked
  // "unread" as long as the conversation has not been opened…
  report.haUnreadBadgeBefore = qa('.conv-row .badge').length > 0;
  const haRow = q('.conv-row');
  if (haRow) { haRow.click(); await waitFor(() => !!q('.conv-input textarea, textarea[placeholder*="Reply"]')); }
  // … then becomes "read" as soon as it is viewed (badge gone, immediately).
  await waitFor(() => qa('.conv-row .badge').length === 0);
  report.haUnreadBadgeAfter = qa('.conv-row .badge').length === 0;
  report.conversationThread = !!q('.conv-thread');
  report.conversationInput = !!q('.conv-input textarea, textarea[placeholder*="Reply"]');

  // Back to Agent ↔ Agent: the switch flips back, no more composer.
  const aaBtn = qa('.conv-mode-switch .seg-btn').find(b => b.textContent.includes('Agent'));
  if (aaBtn) {
    aaBtn.click();
    // Back to Agent ↔ Agent mode (EXACT text — "Human ↔ Agent"
    // also contains "Agent").
    await waitFor(() => (q('.conv-mode-switch .seg-btn.is-active')?.textContent?.trim() || '') === 'Agent ↔ Agent');
  }
  report.backToAA = q('.conv-mode-switch .seg-btn.is-active')?.textContent?.trim() || null;
  await new Promise(r => setTimeout(r, 600));  // detail (empty) render finished
  report.aaComposerAbsentAfterSwitch = !q('.conv-input textarea, textarea[placeholder*="Reply"]');

  // Other views: quick navigation + no errors.
  for (const route of ['#/dashboard', '#/agents', '#/communications', '#/tasks', '#/activity', '#/server', '#/conversations']) {
    nav(route);
    await new Promise(r => setTimeout(r, 700));
    report['route_' + route.slice(2)] = { h1: q('h1')?.textContent?.trim() || null, errors: errors.length };
  }

  report.consoleErrors = errors;
  console.log(JSON.stringify(report, null, 1));
  dom.window.close();
  process.exit(errors.length === 0 ? 0 : 2);
})().catch(e => { console.error('HARNESS FAILED:', e); process.exit(1); });
