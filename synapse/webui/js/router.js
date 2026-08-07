/* ==========================================================================
   Synapse — Routeur (hash) : #/dashboard, #/agents, #/agents/:username, …
   ========================================================================== */

import { el } from './ui.js';

export const NAV_ITEMS = [
  { route: 'dashboard', label: 'Dashboard', icon: 'dashboard', key: 'd' },
  { route: 'agents', label: 'Agents', icon: 'agents', key: 'a' },
  { route: 'communications', label: 'Communications', icon: 'message', key: 'c' },
  { route: 'conversations', label: 'Conversations', icon: 'chat', key: 'v' },
  { route: 'tasks', label: 'Tasks', icon: 'tasks', key: 't' },
  { route: 'activity', label: 'Activity', icon: 'activity', key: 'o' },
  { route: 'organization', label: 'Organization', icon: 'organization', key: 'g' },
  { route: 'server', label: 'Server', icon: 'server', key: 's' },
];

export const NAV_LABELS = Object.fromEntries(NAV_ITEMS.map(i => [i.route, i.label]));

const MATCHERS = [
  { segments: [], view: 'dashboard' },
  { segments: ['dashboard'], view: 'dashboard' },
  { segments: ['agents'], view: 'agents' },
  { segments: ['agents', ':username'], view: 'agent' },
  { segments: ['communications'], view: 'communications' },
  { segments: ['conversations'], view: 'conversations' },
  { segments: ['conversations', ':conversation_id'], view: 'conversations' },
  { segments: ['tasks'], view: 'tasks' },
  { segments: ['activity'], view: 'activity' },
  { segments: ['organization'], view: 'organization' },
  { segments: ['server'], view: 'server' },
];

export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  for (const m of MATCHERS) {
    if (m.segments.length !== raw.length) continue;
    const params = {};
    let ok = true;
    for (let i = 0; i < m.segments.length; i++) {
      if (m.segments[i].startsWith(':')) params[m.segments[i].slice(1)] = decodeURIComponent(raw[i]);
      else if (m.segments[i] !== raw[i]) { ok = false; break; }
    }
    if (ok) return { view: m.view, params };
  }
  return { view: 'dashboard', params: {} };
}

export function routeTitle(view, params) {
  if (view === 'agent' && params.username) return params.username;
  return NAV_LABELS[view] || 'Synapse';
}

/* Breadcrumb: Home › View › [Entity]. Returns ready-to-use <a> or
   <span> items. */
export function breadcrumbsFor(view, params) {
  const home = el('a', { class: 'crumb', href: '#/dashboard', 'aria-label': 'Dashboard' }, 'Accueil');
  if (view === 'dashboard') return [el('span', { class: 'crumb current' }, 'Dashboard')];
  if (view === 'agent' && params.username) {
    return [
      home,
      el('a', { class: 'crumb', href: '#/agents' }, 'Agents'),
      el('span', { class: 'crumb current', text: params.username }),
    ];
  }
  const label = NAV_LABELS[view];
  if (!label) return [home];
  return [home, el('span', { class: 'crumb current' }, label)];
}

export function navigate(route) {
  location.hash = `#/${route}`;
}
