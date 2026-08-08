/* ==========================================================================
   Synapse — Activity view: recent audit journal (no content).
   ========================================================================== */

import { el, clear, badge, emptyState, pageHeader, timeEl, thSortable } from '../ui.js';
import { esc, commandLabel } from '../format.js';
import { api } from '../api.js';

const GROUPS = [
  ['all', 'All'],
  ['messages', 'Messaging'],
  ['tasks', 'Tasks'],
  ['org', 'Organization'],
  ['accounts', 'Accounts'],
];
const GROUP_KEYS = {
  messages: ['send_message', 'send_group_message'],
  tasks: ['create_task', 'update_task_state', 'transfer_task', 'request_approval', 'approve_task', 'reject_task'],
  org: ['create_department', 'set_agent_department', 'set_escalation_policy', 'set_agent_budget', 'set_organization_policy', 'change_organization_password'],
  accounts: ['create_agent', 'deactivate_agent', 'reactivate_agent', 'change_agent_password', 'set_agent_visibility', 'set_agent_card', 'approve_agent_card', 'create_observer_account', 'revoke_observer_account', 'create_group'],
};
let group = 'all';
let sortState = { key: 'at', dir: 'desc' };

function outcomeBadge(outcome) {
  if (!outcome || outcome === 'ok') return badge('ok', 'ok');
  if (outcome === 'failed' || outcome === 'error') return badge('failure', 'danger');
  return badge(outcome, 'neutral', { mono: true });
}

function sortedEntries(entries) {
  const out = [...entries];
  const dir = sortState.dir === 'asc' ? 1 : -1;
  if (sortState.key === 'actor') out.sort((a, b) => String(a.actor_username).localeCompare(String(b.actor_username)) * dir);
  else if (sortState.key === 'command') out.sort((a, b) => String(a.command).localeCompare(String(b.command)) * dir);
  else out.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0) * dir);
  return out;
}

function renderFeed(container) {
  const entries = sortedEntries((api.snapshot?.recent_audit || []).filter(e => {
    if (group === 'all') return true;
    return (GROUP_KEYS[group] || []).includes(e.command);
  }));
  if (!entries.length) {
    container.append(emptyState({
      iconName: 'activity', title: 'No logged actions',
      desc: group === 'all'
        ? 'The audit journal (last 20 actions, no content) will appear here as soon as an agent acts.'
        : 'No actions of this category in the recent journal.',
    }));
    return;
  }
  const setSort = (key, dir) => { sortState = { key, dir }; render(container); };
  container.append(el('div', { class: 'table-wrap' },
    el('table', { class: 'data' },
      el('thead', null, el('tr', null,
        thSortable('Timestamp', 'at', sortState, setSort),
        thSortable('Actor', 'actor', sortState, setSort),
        thSortable('Action', 'command', sortState, setSort),
        el('th', { scope: 'col' }, 'Issue'),
      )),
      el('tbody', null, ...entries.map(e => el('tr', null,
        el('td', { 'data-label': 'Timestamp' }, timeEl(e.at)),
        el('td', { 'data-label': 'Actor' }, el('a', { href: `#/agents/${encodeURIComponent(e.actor_username)}`, class: 'audit-actor', text: e.actor_username })),
        el('td', { 'data-label': 'Action' },
          el('div', { style: 'display:flex;flex-direction:column;gap:2px' },
            el('span', { class: 'audit-cmd', text: e.command }),
            el('span', { class: 'cell-sub', text: commandLabel(e.command) }),
          ),
        ),
        el('td', { 'data-label': 'Issue' }, outcomeBadge(e.outcome)),
      ))),
    ),
    el('div', { class: 'table-foot' },
      el('span', { text: 'the last 20 organization actions — never message content' }),
    ),
  ));
}

export function render(container) {
  const snap = api.snapshot;
  clear(container);
  container.append(pageHeader({
    title: 'Activity',
    desc: 'Organization audit journal: who executed which command, when, with what outcome.',
  }));
  if (!snap) {
    container.append(el('div', { style: 'margin-top:24px' }, el('div', { class: 'skeleton skel-card', style: 'height:320px' })));
    return;
  }
  const toolbar = el('div', { class: 'agents-toolbar' },
    el('div', { class: 'seg', role: 'group', 'aria-label': 'Filter by category' },
      GROUPS.map(([v, label]) =>
        el('button', { 'aria-pressed': group === v ? 'true' : 'false', onclick: () => { group = v; render(container); } }, label)),
    ),
  );
  container.append(toolbar);
  renderFeed(container);
}

export const refresh = render;
