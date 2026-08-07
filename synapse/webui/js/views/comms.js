/* ==========================================================================
   Synapse — Communications view: agent flows (metadata only).
   Accessible column sorting (aria-sort), immediate attention reading.
   ========================================================================== */

import { el, clear, badge, emptyState, pageHeader, flowPair, timeEl,
         protectedBanner, avatarWithStatus, thSortable } from '../ui.js';
import { timeAgo } from '../format.js';
import { api } from '../api.js';

const SORTS = {
  recent: (x, y) => String(y.last_at || '').localeCompare(String(x.last_at || '')),
  volume: (x, y) => (y.message_count || 0) - (x.message_count || 0),
  unread: (x, y) => (y.unread_count || 0) - (x.unread_count || 0),
  a: (x, y) => String(x.a).localeCompare(String(y.a)),
};

let sortState = { key: 'recent', dir: 'desc' };

function sortedConvs() {
  const convs = [...(api.snapshot?.conversations || [])];
  const cmp = SORTS[sortState.key] || SORTS.recent;
  convs.sort((x, y) => {
    const r = cmp(x, y);
    return sortState.dir === 'asc' ? -r : r;
  });
  return convs;
}

function statusCell(c) {
  if ((c.unread_count || 0) > 0) return badge('Reply expected', 'warn', { dot: true });
  return badge('Exchange done', 'ok', { dot: true });
}

function renderTable(container) {
  const convs = sortedConvs();
  if (!convs.length) {
    container.append(emptyState({
      iconName: 'message', title: 'No internal exchanges',
      desc: 'As soon as two organization agents exchange messages, the flow appears here: who talks to whom, with what volume and recency. The content itself stays protected.',
    }));
    return;
  }
  const setSort = (key, dir) => { sortState = { key, dir }; render(container); };
  const tbody = el('tbody');
  for (const c of convs) {
    const row = el('tr', { 'data-row-link': 'true', tabindex: '0',
      onclick: () => { location.hash = `#/agents/${encodeURIComponent(c.a)}`; },
      onkeydown: (e) => { if (e.key === 'Enter') location.hash = `#/agents/${encodeURIComponent(c.a)}`; },
    },
      el('td', {},
        el('div', { style: 'display:flex;align-items:center;gap:10px' },
          avatarWithStatus(c.a, 'active', 'sm'),
          flowPair(c.a, c.b),
        ),
      ),
      el('td', { 'data-label': 'Messages', class: 'cell-num num' }, c.message_count),
      el('td', { 'data-label': 'Unread', class: 'cell-num' },
        (c.unread_count || 0) > 0 ? badge(c.unread_count, 'warn') : el('span', { class: 'cell-sub', text: '0' }),
      ),
      el('td', { 'data-label': 'Last exchange' }, timeEl(c.last_at)),
      el('td', { 'data-label': 'Statut' }, statusCell(c)),
    );
    tbody.append(row);
  }
  const table = el('div', { class: 'table-wrap' },
    el('table', { class: 'data' },
      el('thead', null,
        el('tr', null,
          thSortable('Paire d’agents', 'a', sortState, setSort),
          thSortable('Messages', 'volume', sortState, setSort, { right: true }),
          thSortable('Unread', 'unread', sortState, setSort, { right: true }),
          thSortable('Last exchange', 'recent', sortState, setSort),
          el('th', { scope: 'col' }, 'Statut'),
        ),
      ),
      tbody,
    ),
    el('div', { class: 'table-foot' },
      el('span', { text: `${convs.length} conversation${convs.length > 1 ? 's' : ''} interne${convs.length > 1 ? 's' : ''}` }),
      el('span', { text: 'exchanges with the outside only appear in the volumes' }),
    ),
  );
  container.append(table);
}

export function render(container) {
  const snap = api.snapshot;
  clear(container);
  const totalMsgs = (snap?.conversations || []).reduce((n, c) => n + c.message_count, 0);
  const totalUnread = api.unreadTotal();
  container.append(
    pageHeader({
      title: 'Communications',
      desc: 'Who talks to whom, at what volume, with what recency — and what awaits a reply.',
    }),
    protectedBanner(),
    el('div', { style: 'margin-top:24px' },
      el('div', { class: 'dash-kpis' },
        el('div', { class: 'kpi', 'data-tone': 'info' }, el('span', { class: 'kpi-label' }, 'Conversations'), el('span', { class: 'kpi-value' }, snap?.conversations?.length || 0)),
        el('div', { class: 'kpi', 'data-tone': 'neutral' }, el('span', { class: 'kpi-label' }, 'Messages internes'), el('span', { class: 'kpi-value' }, totalMsgs)),
        el('div', { class: 'kpi', 'data-tone': totalUnread ? 'warn' : 'neutral' }, el('span', { class: 'kpi-label' }, 'Unread'), el('span', { class: 'kpi-value' }, totalUnread)),
        el('div', { class: 'kpi', 'data-tone': 'accent' }, el('span', { class: 'kpi-label' }, 'Messages / heure'), el('span', { class: 'kpi-value' }, snap?.messages_last_hour || 0)),
      ),
    ),
  );

  if (!snap) {
    container.append(el('div', { style: 'margin-top:24px' }, el('div', { class: 'skeleton skel-card', style: 'height:320px' })));
    return;
  }

  container.append(el('div', { style: 'margin-top:24px' }));
  renderTable(container);
}

export const refresh = render;
