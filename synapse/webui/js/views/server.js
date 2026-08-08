/* ==========================================================================
   Synapse — Server view: connection health, human account, shortcuts.
   ========================================================================== */

import { el, clear, badge, pageHeader, card } from '../ui.js';
import { icon } from '../icons.js';
import { api } from '../api.js';

const SHORTCUTS = [
  ['⌘K or /', 'Open the global search'],
  ['g then d', 'Dashboard'],
  ['g then a', 'Agents'],
  ['g then c', 'Communications'],
  ['g then t', 'Tasks'],
  ['g then o', 'Activity'],
  ['g then g', 'Organization'],
  ['g then s', 'Server'],
  ['Esc', 'Close the open window'],
];

function statusCell() {
  const s = api.status;
  const map = {
    live: ['live', 'connected', 'ok'],
    stale: ['stale', 'stale data', 'warn'],
    off: ['off', 'offline', 'danger'],
    connecting: ['stale', 'connecting…', 'warn'],
  };
  const [cls, label] = map[s] || ['off', 'offline'];
  return el('span', { class: `conn-pill ${cls}` }, el('span', { class: 'dot' }), label);
}

function statusGrid() {
  const snap = api.snapshot;
  const snapshotSize = api._snapshotJson ? Math.round(api._snapshotJson.length / 1024) : 0;
  return el('div', { class: 'status-grid' },
    el('div', { class: 'status-cell' }, el('span', { class: 'label' }, 'Connection'), statusCell()),
    el('div', { class: 'status-cell' }, el('span', { class: 'label' }, 'Latency'), el('span', { class: 'value', text: api.latency === null ? '—' : `${api.latency} ms` })),
    el('div', { class: 'status-cell' }, el('span', { class: 'label' }, 'Last update'),
      el('span', { class: 'value', text: api.lastUpdate ? api.lastUpdate.toLocaleTimeString('en-GB') : '—' })),
    el('div', { class: 'status-cell' }, el('span', { class: 'label' }, 'Snapshot'), el('span', { class: 'value', text: snapshotSize ? `~${snapshotSize} KB` : '—' })),
    el('div', { class: 'status-cell' }, el('span', { class: 'label' }, 'Accounts'), el('span', { class: 'value', text: snap?.agents?.length ?? '—' })),
    el('div', { class: 'status-cell' }, el('span', { class: 'label' }, 'Tasks'), el('span', { class: 'value', text: snap?.tasks?.length ?? '—' })),
    el('div', { class: 'status-cell' }, el('span', { class: 'label' }, 'Conversations'), el('span', { class: 'value', text: snap?.conversations?.length ?? '—' })),
    el('div', { class: 'status-cell' }, el('span', { class: 'label' }, 'Polling'), el('span', { class: 'value', text: '5 s' })),
    el('div', { class: 'status-cell' }, el('span', { class: 'label' }, 'Server cache'), el('span', { class: 'value', text: 'ETag · 304' })),
    el('div', { class: 'status-cell' }, el('span', { class: 'label' }, 'Last error'),
      el('span', { class: 'value', style: api.error ? 'color:var(--color-danger);font-size:var(--text-small)' : null, text: api.error ? api.error : 'none' })),
  );
}

function observerCard() {
  const session = api.session;
  return card({
    title: 'Human account', iconName: 'shield',
    body: el('div', {},
      el('dl', { class: 'dl' },
        el('dt', null, 'Name'), el('dd', { class: 'mono', text: session?.human_username || '—' }),
        el('dt', null, 'Organization'), el('dd', { text: api.snapshot?.organization_name || '—' }),
        el('dt', null, 'Rights'), el('dd', null, badge('supervision', 'accent', { dot: true })),
      ),
      el('p', { class: 'cell-sub', style: 'margin-top:12px' },
        'The interface uses the organization human identity: account '
        + 'management and conversation reading (agents have no web access).'),
    ),
  });
}

function shortcutsCard() {
  return card({
    title: 'Keyboard shortcuts', iconName: 'key',
    body: el('div', { class: 'shortcuts-grid' }, ...SHORTCUTS.map(([keys, label]) =>
      el('div', { class: 'shortcut-row' },
        el('span', { text: label }),
        el('span', { class: 'keys' }, ...keys.split(' ').map(k => el('span', { class: 'kbd', text: k }))),
      ),
    )),
  });
}

function aboutCard() {
  return card({
    title: 'About', iconName: 'spark',
    body: el('div', { style: 'display:flex;flex-direction:column;gap:10px' },
      el('p', { class: 'page-desc' },
        'Synapse is a local messaging and coordination infrastructure for AI agent organizations. '
        + 'This supervision interface is served on 127.0.0.1 only; no data leaves the machine.'),
      el('div', { style: 'display:flex;gap:8px;flex-wrap:wrap' },
        badge('API v2', 'neutral', { mono: true }),
        badge('socket Unix local', 'neutral', { mono: true }),
        badge('zero web dependencies', 'ok'),
      ),
    ),
  });
}

export function render(container) {
  clear(container);
  container.append(pageHeader({
    title: 'Server',
    desc: 'State of the link between the interface and the Synapse service, and information about your human account.',
    actions: el('button', { class: 'btn btn-ghost btn-sm', onclick: () => api.poll(), 'aria-label': 'Check connection' }, icon('refresh', 14), 'Check'),
  }));
  container.append(el('div', { style: 'display:flex;flex-direction:column;gap:24px' }, statusGrid(), observerCard(), shortcutsCard(), aboutCard()));
}

export const refresh = render;
