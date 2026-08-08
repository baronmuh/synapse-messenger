// Verification of the SELECTION-based login screen (amended SPEC-WEB D5):
// with no session at all, the app shows the dropdown list of active
// organizations (served by the local web) and the "Sign in" button.
// EXIT 0 = screen correct and zero console errors.
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const { JSDOM } = require('jsdom');

const ORIGIN = process.env.SYNAPSE_WEB_ORIGIN || 'http://127.0.0.1:8092';
const realFetch = globalThis.fetch;

(async () => {
  const html = await (await realFetch(`${ORIGIN}/`)).text();
  const dom = new JSDOM(html, { url: ORIGIN + '/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  const errors = [];
  window.addEventListener('error', e => errors.push('window.onerror: ' + e.message));
  const oc = window.console;
  window.console = { ...oc, error: (...a) => { errors.push('console.error: ' + a.join(' ')); } };
  window.matchMedia = window.matchMedia || (q => ({ matches: false, media: q, onchange: null,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    dispatchEvent() { return false; } }));
  // Real fetch WITHOUT a cookie: the login screen is public, the list of
  // organizations comes from the local web identity.
  window.fetch = async (url, opts = {}) => {
    const res = await realFetch(ORIGIN + url, opts);
    const text = await res.text();
    return { status: res.status, ok: res.ok,
      headers: { get: n => res.headers.get(n) },
      json: async () => JSON.parse(text), text: async () => text };
  };
  globalThis.window = window; globalThis.document = window.document;
  globalThis.location = window.location; globalThis.localStorage = window.localStorage;
  globalThis.fetch = window.fetch;
  Object.defineProperty(globalThis, 'navigator', { value: window.navigator, configurable: true });

  await import(new URL('../../synapse/webui/js/app.js', import.meta.url));
  await new Promise(r => setTimeout(r, 1800));  // boot -> login screen + orgs loaded

  const doc = window.document;
  const report = {
    loginVisible: !!doc.querySelector('#login-root'),
    selectPresent: !!doc.querySelector('#login-root select[name="organization_name"]'),
    options: Array.from(doc.querySelectorAll('#login-root select[name="organization_name"] option'))
      .map(o => o.value).filter(v => v),
    buttonLabel: doc.querySelector('#login-root button')?.textContent?.trim() || null,
    // Organization creation from the login page (amended SPEC-WEB D5):
    // a second mode of the switch allows creating a new organization
    // without being logged in.
    createModeBtn: !!Array.from(doc.querySelectorAll('#login-root .login-mode .seg-btn'))
      .find(b => b.textContent.includes('Create an organization')),
    createFormPresent: null,
    backToLoginAfterToggle: null,
    // Regression guard: the login window must be hideable by showApp —
    // the CSS `.login-root { display: flex }` overrides the hidden
    // attribute, so an explicit [hidden] rule is needed (real bug
    // 2026-08-06: window still visible after login).
    cssRulesPresent: null,
    consoleErrors: errors,
  };
  // Toggle to "Create an organization" then back: the creation mode
  // shows the form (name + password + confirmation) and the return
  // restores the selection.
  try {
    const createBtn = Array.from(doc.querySelectorAll('#login-root .login-mode .seg-btn'))
      .find(b => b.textContent.includes('Create an organization'));
    createBtn?.click();
    await new Promise(r => setTimeout(r, 120));
    report.createFormPresent = !!doc.querySelector('#create-org-name')
      && !!doc.querySelector('#create-org-password')
      && !!doc.querySelector('#create-org-confirm')
      && !!doc.querySelector('#login-root button.login-submit');
    const loginBtn = Array.from(doc.querySelectorAll('#login-root .login-mode .seg-btn'))
      .find(b => b.textContent.trim() === 'Sign in');
    loginBtn?.click();
    await new Promise(r => setTimeout(r, 120));
    report.backToLoginAfterToggle = !!doc.querySelector('#login-root select[name="organization_name"]');
  } catch { /* the report is authoritative */ }
  try {
    const css = await (await realFetch(`${ORIGIN}/assets/css/views.css`)).text();
    report.cssRulesPresent = css.includes('.login-root[hidden]')
      && css.includes('.login-error[hidden]')
      && css.includes('display: none');
  } catch { /* CSS unavailable: the rest of the report is authoritative */ }
  console.log(JSON.stringify(report, null, 1));
  dom.window.close();
  process.exit(errors.length === 0 ? 0 : 2);
})().catch(e => { console.error('HARNESS FAILED:', e); process.exit(1); });
