/* ==========================================================================
   Synapse — Groups view: list of groups + detail (members + messages).

   mirrors conversations.js patterns: fluid refresh, fingerprint comparison.
   ========================================================================== */

import { el, clear, badge, emptyState, toast } from '../ui.js';
import { icon } from '../icons.js';
import { esc, timeAgo } from '../format.js';
import { api } from '../api.js';

let currentId = null;
let listFingerprint = '';
let detailFingerprint = '';

function fingerprintList(list) {
  return list.map(g => `${g.group_id}:${g.name}:${g.member_count}`).join('|');
}

function fingerprintDetail(data) {
  const m = data.messages || [];
  return m.map(x => `${x.created_at}:${x.sender_username}:${x.content}`).join('|');
}

export async function render(root, params) {
  clear(root);
  currentId = params.group_id || null;

  const listWrap = el('div', { class: 'conv-list', 'aria-label': 'Groups' });
  const detailWrap = el('div', { class: 'conv-detail' });

  root.append(el('div', { class: 'page-head' },
    el('div', null,
      el('h1', { class: 'page-title' }, 'Groups'),
      el('p', { class: 'page-sub' },
        'Organization discussion groups — shared channels with members.'),
    ),
  ));
  root.append(el('div', { class: 'conv-layout' }, listWrap, detailWrap));

  await renderList(listWrap, currentId, detailWrap, true);
  if (currentId) {
    await renderDetail(detailWrap, currentId, true);
  } else {
    detailWrap.append(emptyState('Select a group to see its messages.',
      'Content is only loaded on demand.'));
  }

  // fluid refresh
  setInterval(async () => {
    const live = document.querySelector('#content');
    if (!live) return;
    const lw = live.querySelector('.conv-list');
    const dw = live.querySelector('.conv-detail');
    if (lw) await renderList(lw, currentId, dw, false);
    if (dw && currentId) await renderDetail(dw, currentId, false);
  }, 3000);
}

async function renderList(listWrap, requested, detailWrap, force) {
  let data;
  try {
    data = await api.groups();
  } catch (e) {
    toast('error', e.message || 'Failed to load groups');
    return;
  }
  const fp = fingerprintList(data.groups || []);
  if (!force && fp === listFingerprint) return;
  listFingerprint = fp;

  clear(listWrap);
  const groups = data.groups || [];
  if (!groups.length) {
    listWrap.append(emptyState('No groups yet', 'Create one from the CLI.'));
    return;
  }
  const ul = el('ul', { class: 'list' });
  for (const g of groups) {
    const active = g.group_id === requested ? ' is-active' : '';
    ul.append(el('li', { class: 'list-item' + active },
      el('a', { href: `#/groups/${g.group_id}`, class: 'list-link' },
        el('span', { class: 'list-name' }, g.name || g.group_id),
        g.member_count != null ? badge(g.member_count, 'count') : null)));
  }
  listWrap.append(ul);
}

async function renderDetail(detailWrap, groupId, force) {
  let data;
  try {
    data = await api.group(groupId);
  } catch (e) {
    detailWrap.replaceChildren(emptyState('Group not found', e.message || 'Not accessible.'));
    return;
  }
  const fp = fingerprintDetail(data);
  if (!force && fp === detailFingerprint) return;
  detailFingerprint = fp;

  clear(detailWrap);
  const members = data.members || [];
  const messages = data.messages || [];

  const header = el('div', { class: 'detail-head' },
    el('h2', { class: 'detail-title' }, 'Group'),
    el('div', { class: 'members' },
      members.map(m => el('span', { class: 'chip' }, m.username)).length
        ? members.map(m => el('span', { class: 'chip' }, m.username))
        : el('span', { class: 'muted' }, 'No members.')));
  detailWrap.append(header);

  const feed = el('div', { class: 'messages' });
  if (!messages.length) {
    feed.append(emptyState('No messages yet', 'Members can send messages to this group.'));
  } else {
    for (const m of messages) {
      feed.append(el('div', { class: 'message' },
        el('div', { class: 'message-head' },
          el('span', { class: 'message-sender' }, m.sender_username),
          el('span', { class: 'message-time' }, timeAgo(m.created_at))),
        el('div', { class: 'message-content' }, m.content)));
    }
  }
  detailWrap.append(feed);
}

export const refresh = render;
