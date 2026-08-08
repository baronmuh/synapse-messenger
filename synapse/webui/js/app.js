/* ==========================================================================
   Synapse — Application: "Ledger" shell, router, palette, bell,
   shortcuts. The v3 shell: brand bar (N9 edge-aligned) + view tab
   strip. The v2 sidebar is gone.
   ========================================================================== */

import { icon, brandMark } from './icons.js';
import { esc } from './format.js';
import { el, clear, badge, avatarWithStatus, openModal, toast } from './ui.js';
import { api } from './api.js';
import { NAV_ITEMS, NAV_LABELS, parseHash, routeTitle } from './router.js';
import * as views from './views/index.js';
import { render as renderLogin } from './views/login.js';

const content = document.getElementById('content');
const brandbar = document.getElementById('brandbar');
const tabstrip = document.getElementById('tabstrip');
const paletteRoot = document.getElementById('palette-root');
const loginRoot = document.getElementById('login-root');
const appRoot = document.getElementById('app');

let current = { view: 'dashboard', params: {} };
let paletteOpen = false;
let pendingKeys = '';
let bellOpen = false;

/* ==========================================================================
   "Ledger" shell
   ========================================================================== */

function navCount(route) {
  if (!api.snapshot) return 0;
  if (route === 'tasks') return api.taskCounts().active;
  if (route === 'communications') return api.unreadTotal();
  if (route === 'conversations') {
    return (api.snapshot.conversations || []).filter(c => (c.unread_count || 0) > 0).length;
  }
  return 0;
}

/* ---- Tab strip tab ------------------------------------------------- */
function tabEl(item, active) {
  const count = navCount(item.route);
  return el('button', {
    class: 'tab', role: 'tab', type: 'button',
    'aria-current': active ? 'page' : null,
    'aria-label': item.label,
    onclick: () => { location.hash = `#/${item.route}`; },
  },
    el('span', { class: 'tab-icon', html: icon(item.icon, 16) }),
    el('span', { class: 'tab-label', text: item.label }),
    count > 0 ? el('span', { class: 'tab-count', 'aria-label': `${count} item${count > 1 ? 's' : ''} to process` }, count) : null,
  );
}

function renderTabstrip() {
  clear(tabstrip);
  NAV_ITEMS.forEach(item => {
    tabstrip.append(tabEl(item, current.view === item.route || (current.view === 'agent' && item.route === 'agents')));
  });
}

/* ---- Brand bar ------------------------------------------------------ */
function renderBrandbar() {
  clear(brandbar);
  const snap = api.snapshot;
  const orgName = snap?.organization_name || '…';
  const human = api.session?.human_username || '—';

  brandbar.append(
    el('button', {
      class: 'brand', type: 'button', 'aria-label': 'Dashboard',
      onclick: () => { location.hash = '#/dashboard'; },
    },
      el('span', { html: brandMark(26) }),
      el('span', { class: 'brand-name' }, 'Synapse', el('span', { class: 'brand-sup' }, ' · Supervision')),
      el('span', { class: 'brand-org', title: orgName }, orgName),
    ),
    el('span', { class: 'brandbar-spacer' }),
    el('span', { class: 'session-identity' },
      el('span', { class: 'identity-name', text: human }),
      el('span', { class: 'identity-sep', text: '·' }),
      el('span', { class: 'identity-org', text: orgName }),
    ),
    el('button', {
      class: 'search-trigger', onclick: () => openPalette(),
      'aria-label': 'Global search (Ctrl+K)',
    },
      el('span', { style: 'display:inline-flex', html: icon('search', 15) }),
      el('span', { text: 'Search…' }),
      el('span', { class: 'kbd', text: '⌘K' }),
    ),
    connPillEl(),
    bellEl(),
    el('button', {
      class: 'btn btn-ghost btn-sm', type: 'button',
      'aria-label': 'Sign out', title: 'Switch organization (sign out)',
      onclick: async () => {
        await api.logout();
        showLogin();
      },
    }, icon('logout', 14), 'Sign out'),
  );
}

function connPillEl() {
  const s = api.status;
  const cls = s === 'live' ? 'live' : s === 'stale' ? 'stale' : 'off';
  const label = s === 'live' ? 'live' : s === 'stale' ? 'stale data' : 'offline';
  const pill = el('span', {
    class: `conn-pill ${cls}`, role: 'status',
    title: api.error ? `Error: ${api.error}` : `Last update: ${api.lastUpdate?.toLocaleTimeString('en-GB') || '—'}`,
    onclick: () => api.poll(),
    style: 'cursor:pointer',
  }, el('span', { class: 'dot' }), label);
  return pill;
}

function attentionItems() {
  const items = [];
  const snap = api.snapshot;
  if (!snap) return items;
  const overdue = api.overdueTasks(3);
  for (const t of overdue) {
    items.push({ href: '#/tasks', tone: 'danger', icon: 'clock',
      title: `Late task (#${t.task_id.slice(0, 8)})`, sub: `assigned to ${t.assignee_username}` });
  }
  const pending = snap.tasks_by_state?.pending_approval || 0;
  if (pending > 0) items.push({ href: '#/tasks', tone: 'warn', icon: 'shield',
    title: `${pending} task${pending > 1 ? 's' : ''} pending approval`, sub: 'a decision is awaited' });
  for (const c of api.conversationsNeedingReply(3)) {
    items.push({ href: '#/communications', tone: 'warn', icon: 'message',
      title: `${c.unread_count} message${c.unread_count > 1 ? 's' : ''} unread${c.unread_count > 1 ? 's' : ''}`,
      sub: `${c.a} ⇄ ${c.b}` });
  }
  const inactive = snap.agents.filter(a => a.status !== 'active').length;
  if (inactive > 0) items.push({ href: '#/agents', tone: 'info', icon: 'user',
    title: `${inactive} agent${inactive > 1 ? 's' : ''} inactive${inactive > 1 ? 's' : ''}`, sub: 'see the directory' });
  return items;
}

function bellEl() {
  const count = api.unreadTotal() + (api.snapshot?.tasks_by_state?.pending_approval || 0) + api.overdueTasks().length;
  const wrap = el('div', { class: 'bell-wrap' });
  const btn = el('button', {
    class: 'icon-btn', 'aria-label': count ? `${count} items to process` : 'Notifications',
    onclick: (e) => { e.stopPropagation(); toggleBell(wrap); },
  }, icon('bell', 18));
  if (count > 0) btn.append(el('span', { class: 'pill-count', style: 'position:absolute;top:-4px;right:-4px', text: count > 99 ? '99+' : count }));
  wrap.append(btn);
  return wrap;
}

function toggleBell(wrap) {
  const existing = wrap.querySelector('.popover');
  if (existing) { existing.remove(); bellOpen = false; return; }
  document.querySelectorAll('.popover').forEach(p => p.remove());
  bellOpen = true;
  const items = attentionItems();
  const pop = el('div', { class: 'popover', role: 'dialog', 'aria-label': 'Items to process' },
    el('div', { class: 'popover-header' },
      el('span', null, 'To process'),
      el('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => pop.remove() }, icon('close', 14)),
    ),
    items.length === 0
      ? el('div', { class: 'palette-empty', text: 'Nothing needs your attention.' })
      : el('div', { class: 'list-plain' }, ...items.map(it =>
        el('a', { class: 'list-row', href: it.href, onclick: () => pop.remove() },
          el('span', { class: `att-icon ${it.tone === 'danger' ? 'att-danger' : it.tone === 'warn' ? 'att-warn' : 'att-info'}`, html: icon(it.icon) }),
          el('div', { style: 'flex:1;min-width:0' },
            el('div', { class: 'row-title', text: it.title }),
            el('div', { class: 'row-sub', text: it.sub }),
          ),
        ),
      )),
  );
  wrap.append(pop);
}

/* ==========================================================================
   Routeur
   ========================================================================== */

function renderView() {
  const { view, params } = parseHash();
  current = { view, params };
  const fn = views[view] || views.dashboard;
  renderBrandbar();
  renderTabstrip();
  document.title = `${routeTitle(view, params)} — Synapse · Supervision`;
  try {
    Promise.resolve(fn(content, params)).catch(e => {
      console.error(e);
      clear(content);
      content.append(el('div', { class: 'banner banner-danger', role: 'alert' },
        el('span', { class: 'banner-icon', html: icon('error') }),
        el('span', { html: 'Cannot display this view. ' + esc(e.message || 'unknown error') }),
      ));
    });
  } catch (e) {
    console.error(e);
  }
}

function onSnapshotUpdate() {
  renderBrandbar();
  renderTabstrip();
  // Refresh the current view if it is visible and the user
  // is not interacting (focus in a field).
  const active = document.activeElement;
  const interacting = active && content.contains(active) && active.tagName !== 'BODY';
  if (interacting) return;
  const refreshFn = views[`refresh${current.view[0].toUpperCase()}${current.view.slice(1)}`];
  if (refreshFn) {
    try { refreshFn(content, current.params); } catch (e) { console.error(e); }
  }
}

/* ==========================================================================
   Command palette (⌘K / /)
   ========================================================================== */

let paletteResults = [];

function openPalette() {
  if (paletteOpen) return;
  paletteOpen = true;
  const input = el('input', {
    class: 'palette-input', type: 'search', placeholder: 'Search an agent, a view…',
    'aria-label': 'Global search',
  });
  const results = el('div', { class: 'palette-results', role: 'listbox' });
  const box = el('div', { class: 'palette-box', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Global search' },
    input, results);
  const overlay = el('div', {
    class: 'palette', onclick: (e) => { if (e.target === overlay) close(); },
  }, box);
  paletteRoot.append(overlay);

  const close = () => {
    paletteOpen = false;
    overlay.remove();
    document.removeEventListener('keydown', onKey);
    document.getElementById('brandbar')?.querySelector('.search-trigger')?.focus();
  };

  const select = (item) => {
    if (!item) return;
    close();
    if (item.href) location.hash = item.href;
  };

  const renderResults = (items) => {
    clear(results);
    paletteResults = items;
    if (!items.length) {
      results.append(el('div', { class: 'palette-empty', text: 'No results. Try an agent name or a capability.' }));
      return;
    }
    const grouped = new Map();
    for (const it of items) {
      if (!grouped.has(it.group)) grouped.set(it.group, []);
      grouped.get(it.group).push(it);
    }
    for (const [group, list] of grouped) {
      results.append(el('div', { class: 'palette-group-label' }, group));
      list.forEach(it => results.append(el('button', {
        class: 'palette-item', role: 'option', 'aria-selected': 'false',
        onmouseenter: (e) => e.currentTarget.setAttribute('aria-selected', 'true'),
        onmouseleave: (e) => e.currentTarget.setAttribute('aria-selected', 'false'),
        onclick: () => select(it),
      },
        el('span', { class: 'palette-icon', html: icon(it.icon) }),
        el('span', { text: it.label }),
        it.meta ? el('span', { class: 'palette-meta', text: it.meta }) : null,
      )));
    }
  };

  const onKey = (e) => {
    if (e.key === 'Escape') { e.stopPropagation(); close(); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const items = [...results.querySelectorAll('.palette-item')];
      const idx = items.findIndex(i => i.getAttribute('aria-selected') === 'true');
      const next = e.key === 'ArrowDown' ? Math.min(idx + 1, items.length - 1) : Math.max(idx - 1, 0);
      items.forEach(i => i.setAttribute('aria-selected', 'false'));
      if (items[next]) { items[next].setAttribute('aria-selected', 'true'); items[next].scrollIntoView({ block: 'nearest' }); }
      return;
    }
    if (e.key === 'Enter') {
      const sel = results.querySelector('.palette-item[aria-selected="true"]');
      if (sel) select(paletteResults[[...results.querySelectorAll('.palette-item')].indexOf(sel)]);
      else if (paletteResults.length === 1) select(paletteResults[0]);
    }
  };

  const runSearch = async (q) => {
    const navItems = NAV_ITEMS
      .filter(i => !q || i.label.toLowerCase().includes(q.toLowerCase()))
      .map(i => ({ group: 'Navigation', icon: i.icon, label: i.label, meta: `g ${i.key}`, href: `#/${i.route}` }));
    const agentItems = (api.snapshot?.agents || [])
      .filter(a => !q || a.username.toLowerCase().includes(q.toLowerCase()) || (a.description || '').toLowerCase().includes(q.toLowerCase()))
      .slice(0, 8)
      .map(a => ({ group: 'Agents', icon: 'agents', label: a.username, meta: a.status, href: `#/agents/${encodeURIComponent(a.username)}` }));
    renderResults([...navItems, ...agentItems]);
    // Capability search via the API (find_agents) beyond 2 characters.
    if (q.length >= 2) {
      try {
        const res = await fetch(`/api/search?capability=${encodeURIComponent(q)}`, { cache: 'no-cache' });
        if (!res.ok) return;
        const { data } = await res.json();
        const found = (data.agents || []).map(a => ({
          group: 'Capabilities', icon: 'spark', label: a.username,
          meta: (a.capabilities || []).slice(0, 2).join(', '),
          href: `#/agents/${encodeURIComponent(a.username)}`,
        }));
        const existing = new Set(agentItems.map(i => i.label));
        const extra = found.filter(i => !existing.has(i.label));
        if (extra.length) renderResults([...navItems, ...agentItems, ...extra]);
      } catch { /* silent: the local search suffices */ }
    }
  };

  document.addEventListener('keydown', onKey);
  input.addEventListener('input', (e) => runSearch(e.target.value));
  setTimeout(() => input.focus(), 30);
  runSearch('');
}

/* ==========================================================================
   Global keyboard shortcuts
   ========================================================================== */

document.addEventListener('keydown', (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); openPalette(); return; }
  if (e.key === '/' && document.activeElement?.tagName !== 'INPUT') { e.preventDefault(); openPalette(); return; }
  if (e.key === '?' && !mod) { openShortcuts(); return; }
  if (e.key === 'Escape' && bellOpen) { document.querySelectorAll('.popover').forEach(p => p.remove()); bellOpen = false; return; }
  if (mod || e.altKey) return;
  if (document.activeElement?.tagName === 'INPUT' || document.activeElement?.tagName === 'TEXTAREA') return;
  if (e.key === 'g') { pendingKeys = 'g'; setTimeout(() => { pendingKeys = ''; }, 800); return; }
  if (pendingKeys === 'g' && /^[a-z]$/.test(e.key)) {
    const item = NAV_ITEMS.find(i => i.key === e.key);
    if (item) { location.hash = `#/${item.route}`; }
    pendingKeys = '';
  }
});

function openShortcuts() {
  const rows = [
    ['⌘K or /', 'Global search'],
    ['g then d / a / c / t / o / g / s', 'Navigate between views'],
    ['Esc', 'Close the open window'],
    ['Enter', 'Open the selected item'],
    ['r', 'Refresh the data'],
  ];
  const grid = el('div', { class: 'shortcuts-grid' });
  for (const [keys, label] of rows) {
    const kbdRow = el('div', { class: 'shortcut-row' }, el('span', { text: label }), el('span', { class: 'keys' }));
    for (const k of keys.split(' / ')) kbdRow.querySelector('.keys').append(el('span', { class: 'kbd', text: k }));
    grid.append(kbdRow);
  }
  openModal({
    title: 'Keyboard shortcuts',
    body: grid,
    actions: [el('button', { class: 'btn btn-secondary', onclick: () => document.querySelector('.blanket')?.remove() }, 'Close')],
  });
}

/* ==========================================================================
   Startup
   ========================================================================== */

function onVisibility() {
  if (document.hidden) { api.stop(); } else { api.start(); api.poll(); }
}

window.addEventListener('hashchange', renderView);
document.addEventListener('click', () => {
  if (bellOpen) { document.querySelectorAll('.popover').forEach(p => p.remove()); bellOpen = false; }
});

// Shortcut r: refresh (outside input fields).
document.addEventListener('keydown', (e) => {
  if (e.key === 'r' && !e.metaKey && !e.ctrlKey && !e.altKey &&
      document.activeElement?.tagName !== 'INPUT' && document.activeElement?.tagName !== 'TEXTAREA') {
    api.poll();
  }
});

api.onUpdate(onSnapshotUpdate);

/* ==========================================================================
   Session gate (SPEC-WEB §6): the app is only mounted with a
   valid session; any expiration returns to the login screen.
   ========================================================================== */

function showLogin() {
  api.stop();
  appRoot.hidden = true;
  loginRoot.hidden = false;
  renderLogin(loginRoot);
}

function showApp() {
  loginRoot.hidden = true;
  appRoot.hidden = false;
}

api.onSessionChange((session) => {
  if (session) {
    showApp();
    renderBrandbar();
    renderTabstrip();
    if (!location.hash) location.hash = '#/dashboard';
    renderView();
    api.start();
  } else {
    showLogin();
  }
});

async function boot() {
  const authed = await api.checkSession();
  if (!authed) {
    showLogin();
    return;
  }
  await api.bootstrap();
  renderBrandbar();
  renderTabstrip();
  renderView();
  document.addEventListener('visibilitychange', onVisibility);
  if (api.status === 'off') {
    toast('error', 'Server unreachable — the interface retries automatically every 5s.', { duration: 8000 });
  }
}

boot();
