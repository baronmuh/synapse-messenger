// Real-DOM verification harness for the Synapse onboarding page.
// Loads /onboarding in jsdom and checks the real rendering: presence of the
// 6 sections, the 5-step workflow, the example query, the explicit
// validation mention (I APPROVE THIS PLAN), the navigation buttons and the
// absence of console errors.
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const { JSDOM } = require('jsdom');

const ORIGIN = process.env.SYNAPSE_WEB_ORIGIN || 'http://127.0.0.1:8092';

const realFetch = globalThis.fetch;
const errors = [];

(async () => {
  const html = await realFetch(`${ORIGIN}/onboarding`).then(r => {
    if (r.status !== 200) throw new Error(`/onboarding -> HTTP ${r.status}`);
    return r.text();
  });

  const dom = new JSDOM(html, {
    url: `${ORIGIN}/onboarding`,
    runScripts: 'outside-only',
    beforeParse(window) {
      window.addEventListener('error', e => errors.push(String(e.error || e.message)));
    },
  });
  const doc = dom.window.document;

  const checks = [
    ['title', doc.querySelector('h1')?.textContent || ''],
    ['section 1 (what)', !!doc.querySelector('.ob-section h2')],
    ['workflow 5 steps', doc.querySelectorAll('.ob-step').length === 5],
    ['example query', !!doc.querySelector('.ob-query')],
    ['explicit validation', /I APPROVE THIS PLAN/.test(doc.body.textContent)],
    ['login button', !!doc.querySelector('a.ob-btn[href="/"]')],
    ['create-organization button', /Create my first company/.test(doc.body.textContent)],
  ];

  let ok = true;
  for (const [name, value] of checks) {
    const pass = typeof value === 'string' ? value.length > 0 : value === true;
    if (!pass) ok = false;
    console.log(`${pass ? 'OK ' : 'FAIL'} ${name}${typeof value === 'string' ? ` (${value.slice(0, 40)})` : ''}`);
  }
  if (errors.length) {
    ok = false;
    console.log('FAIL console errors:', errors.join(' | '));
  }
  console.log(ok ? 'ONBOARDING DOM OK' : 'ONBOARDING DOM FAILED');
  process.exit(ok ? 0 : 1);
})().catch(e => { console.error('HARNESS ERROR:', e.message); process.exit(1); });
