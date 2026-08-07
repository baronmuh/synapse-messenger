/* ==========================================================================
   Synapse — Conversations view (SPEC-WEB §2, option B, reorganized):
   switch [Agent ↔ Agent | Humain ↔ Agent].

   * Agent ↔ Agent     : consultation en lecture seule (aucun composeur).
   * Humain ↔ Agent    : consultation + envoi de messages (compte humain).

   FLUID refresh: data is compared by fingerprint
   (ids, counters, timestamps); if nothing changed, the DOM is NOT
   touched — no reload, no "Loading…" message, no flicker,
   no scroll/selection loss. Re-rendering only happens when the content
   really changes (new message, unread, etc.).
   ========================================================================== */

import { el, clear, badge, emptyState, toast } from '../ui.js';
import { icon } from '../icons.js';
import { esc, timeAgo } from '../format.js';
import { api } from '../api.js';

const MODE_AA = 'aa';  // Agent ↔ Agent (lecture seule)
const MODE_HA = 'ha';  // Humain ↔ Agent (consultation + envoi)

let mode = MODE_AA;
let currentId = null;
let currentConversation = null;
let listFingerprint = '';
let detailFingerprint = '';

function fingerprintList(list) {
  return list.map(c => `${c.conversation_id}:${c.message_count}:${c.last_message_at}:${c.unread_count}`).join('|');
}

function fingerprintDetail(data) {
  return (data.messages || [])
    .map(m => `${m.created_at}:${m.sender_username}:${m.content}`).join('|')
    + '#' + (data.next_cursor || '');
}

function isHuman(username) {
  return !!api.session && username === api.session.human_username;
}

/** Filtre les conversations selon le mode : l'humain courant participe (HA)
 *  or not (AA). Multi-org consistent: the list is already the org's. */
function filterByMode(conversations) {
  if (mode === MODE_HA) {
    return conversations.filter(c => (c.participants || []).some(isHuman));
  }
  return conversations.filter(c => !(c.participants || []).some(isHuman));
}

export async function render(root, params) {
  clear(root);
  const requested = params.conversation_id || null;
  currentId = requested;

  const listWrap = el('div', { class: 'conv-list', 'aria-label': 'Conversations' });
  const detailWrap = el('div', { class: 'conv-detail' });

  root.append(el('div', { class: 'page-head' },
    el('div', null,
      el('h1', { class: 'page-title' }, 'Conversations'),
      el('p', { class: 'page-sub' },
        'Organization exchanges — viewing conversations between agents, ' +
        'viewing and replying to conversations with a human.'),
    ),
    el('div', { class: 'segmented conv-mode-switch', role: 'group',
      'aria-label': 'Conversation type' },
      el('button', { class: 'seg-btn' + (mode === MODE_AA ? ' is-active' : ''), type: 'button',
        'aria-pressed': mode === MODE_AA,
        onclick: () => switchMode(MODE_AA) },
        'Agent ↔ Agent'),
      el('button', { class: 'seg-btn' + (mode === MODE_HA ? ' is-active' : ''), type: 'button',
        'aria-pressed': mode === MODE_HA,
        onclick: () => switchMode(MODE_HA) },
        'Human ↔ Agent'),
    ),
  ));
  root.append(el('div', { class: 'conv-layout' }, listWrap, detailWrap));

  renderList(listWrap, requested, detailWrap, true);
  if (requested) {
    await renderDetail(detailWrap, requested, true);
  } else {
    detailWrap.append(emptyState('Select a conversation to read its content.',
      'Content is only loaded on demand (never in the polling).'));
  }
}

async function switchMode(next) {
  if (next === mode) return;
  mode = next;
  // The mode changed: the displayed content changes — force re-render.
  listFingerprint = '';
  detailFingerprint = '';
  const root = document.querySelector('#content');
  const listWrap = root?.querySelector('.conv-list');
  const detailWrap = root?.querySelector('.conv-detail');
  if (listWrap) await renderList(listWrap, currentId, detailWrap, true);
  // The selected conversation may not belong to the new mode:
  // if it is no longer in the filtered list, start from the list.
  if (detailWrap && currentId) {
    const stillThere = listWrap?.querySelector(
      `.conv-row[href$="${encodeURIComponent(currentId)}"]`);
    if (!stillThere) {
      currentId = null;
      detailFingerprint = '';
      location.hash = '#/conversations';  // liste du nouveau mode
      return;
    }
    await renderDetail(detailWrap, currentId, true);
  } else if (detailWrap) {
    clear(detailWrap);
    detailWrap.append(emptyState('Select a conversation to read its content.',
      'Content is only loaded on demand.'));
  }
}

async function renderList(container, selectedId, detailWrap, force) {
  try {
    const conversations = await api.conversations();
    const filtered = filterByMode(conversations);
    const fp = fingerprintList(filtered);
    // Nothing changed and the list is already displayed: do NOT touch the
    // DOM (no flicker, no scroll/selection loss).
    if (!force && fp === listFingerprint && container.querySelector('.conv-row')) {
      return;
    }
    listFingerprint = fp;
    clear(container);
    if (!filtered.length) {
      container.append(emptyState(
        mode === MODE_HA ? 'Aucune conversation humain ↔ agent.'
                         : 'Aucune conversation entre agents.',
        mode === MODE_HA
          ? 'Exchanges involving a human account of the organization will appear here.'
          : 'Exchanges between the organization agents will appear here.'));
      return;
    }
    for (const c of filtered) {
      const me = api.session?.human_username;
      const participants = c.participants || [];
      const other = participants.find(u => u !== me) || participants[0] || '?';
      // En Agent ↔ Agent, on montre les deux interlocuteurs ; en Humain ↔
      // Agent, le nom de l'agent (l'humain est « moi »).
      const displayName = mode === MODE_HA ? other : participants.join(' ↔ ');
      const row = el('a', {
        class: 'conv-row' + (c.conversation_id === selectedId ? ' active' : ''),
        href: `#/conversations/${encodeURIComponent(c.conversation_id)}`,
        'aria-label': `Conversation with ${displayName} — ${c.message_count} message${c.message_count > 1 ? 's' : ''}`,
      },
        avatarDot(displayName),
        el('div', { style: 'flex:1;min-width:0' },
          el('div', { class: 'conv-row-top' },
            el('span', { class: 'conv-row-name', text: displayName }),
            el('span', { class: 'cell-sub', text: timeAgo(c.last_message_at) }),
          ),
          el('div', { class: 'conv-row-sub' },
            el('span', { class: 'cell-sub', text: `${c.message_count} message${c.message_count > 1 ? 's' : ''}` }),
            c.unread_count > 0
              ? badge(`${c.unread_count} unread${c.unread_count > 1 ? 's' : ''}`, 'warn', { dot: true })
              : null,
          ),
        ),
      );
      container.append(row);
    }
    void detailWrap;
  } catch (e) {
    clear(container);
    container.append(el('div', { class: 'banner banner-danger', role: 'alert' },
      el('span', { class: 'banner-icon', html: icon('error') }),
      el('span', { text: esc(e.message || 'cannot load the conversations') }),
    ));
  }
}

function avatarDot(username) {
  const initial = (username || '?')[0].toUpperCase();
  return el('span', {
    class: 'conv-avatar', 'aria-hidden': 'true',
    title: username,
  }, initial);
}

async function renderDetail(container, conversation_id, force) {
  // No looping "Loading content…": it only appears if the
  // detail was never displayed yet (first load).
  if (!container.querySelector('.conv-thread, .conv-detail-loading')) {
    container.append(el('div', { class: 'conv-detail-loading', text: 'Loading content…' }));
  }
  try {
    const data = await api.conversation(conversation_id);
    const fp = fingerprintDetail(data);
    // The content did not change: the already-displayed detail is kept as
    // quel (pas de clignotement, pas de perte de scroll ni de focus).
    if (!force && fp === detailFingerprint && container.querySelector('.conv-thread')) {
      return;
    }
    detailFingerprint = fp;
    currentConversation = data;
    clear(container);
    const me = api.session?.human_username;
    const messages = data.messages || [];
    const participants = [...new Set(messages.map(m => m.sender_username)
      .concat(messages.map(m => m.recipient_username)))];
    const other = participants.find(u => u !== me) || me || '?';
    // Conversation sides (messaging presentation): in the Human ↔
    // Agent view, "me" (the human) is on the right; in the Agent ↔ Agent view (read-
    // only), both interlocutors share the sides — the first
    // participant on the left, the second on the right (clean bubble alternation).
    const sideB = mode === MODE_HA ? me : (participants[1] || participants[0]);
    const headName = mode === MODE_HA ? other : participants.join(' ↔ ');

    const head = el('div', { class: 'conv-detail-head' },
      el('div', { style: 'display:flex;align-items:center;gap:10px;min-width:0' },
        avatarDot(headName),
        el('div', { style: 'min-width:0' },
          el('div', { class: 'conv-detail-name', text: headName }),
          el('div', { class: 'cell-sub', text: `${messages.length} message${messages.length > 1 ? 's' : ''} loaded${data.next_cursor ? ' — next page available' : ''}` }),
        ),
      ),
    );
    const thread = el('div', { class: 'conv-thread', role: 'log', 'aria-label': 'Messages' });
    for (const m of messages) {
      const mine = m.sender_username === sideB;
      thread.append(el('div', { class: 'msg-row ' + (mine ? 'mine' : 'theirs') },
        el('div', { class: 'msg-bubble' },
          el('div', { class: 'msg-head' },
            el('span', { class: 'msg-sender', text: m.sender_username }),
            el('span', { class: 'cell-sub', text: timeAgo(m.created_at) }),
          ),
          el('div', { class: 'msg-content', text: m.content }),
        ),
      ));
    }
    if (!messages.length) thread.append(emptyState('Aucun message dans cette conversation.'));

    container.append(head, thread);

    // UNREAD handling (messaging): viewing marked as read the
    // messages addressed to the human (server-side, when reading the detail).
    // The read state is immediately reflected on the local counter (sidebar)
    // et sur la liste (badge), sans attendre le polling.
    if (api.snapshot && Array.isArray(api.snapshot.conversations)) {
      let changed = false;
      for (const c of api.snapshot.conversations) {
        if (c.conversation_id === conversation_id && (c.unread_count || 0) > 0) {
          c.unread_count = 0;
          changed = true;
        }
      }
      if (changed) api._emit();  // sidebar + list (unread badge disappears)
    }

    // Composeur UNIQUEMENT en vue Humain ↔ Agent : la vue Agent ↔ Agent
    // est en lecture seule.
    if (mode === MODE_HA) {
      const composer = el('form', {
        class: 'conv-composer',
        onsubmit: async (e) => {
          e.preventDefault();
          const text = input.value.trim();
          if (!text) return;
          sendBtn.disabled = true;
          try {
            await api.sendMessage(other, text);
            input.value = '';
            // Silent success: the message appears in the thread (redesign v2).
            detailFingerprint = '';  // force le re-rendu avec le nouveau message
            await renderDetail(container, conversation_id, true);
          } catch (err) {
            toast('error', err.message || 'Send failed');
          } finally {
            sendBtn.disabled = false;
          }
        },
      });
      const input = el('textarea', {
        class: 'conv-input', rows: 2, placeholder: `Reply to ${other}…`,
        'aria-label': `Reply to ${other}`,
      });
      const sendBtn = el('button', { type: 'submit', class: 'btn btn-primary' },
        el('span', { style: 'display:inline-flex', html: icon('send', 14) }),
        el('span', { text: 'Envoyer' }));
      composer.append(input, sendBtn);
      container.append(composer);
    }
    const threadEl = container.querySelector('.conv-thread');
    if (threadEl) threadEl.scrollTop = threadEl.scrollHeight;
  } catch (e) {
    clear(container);
    container.append(el('div', { class: 'banner banner-danger', role: 'alert' },
      el('span', { class: 'banner-icon', html: icon('error') }),
      el('span', { text: esc(e.message || 'conversation not found') }),
    ));
  }
}

export async function refresh(root, params) {
  // FLUID periodic refresh: no force — if the data
  // did not change, no re-render (no "Loading…", no flicker).
  if (currentId) {
    const detailWrap = root.querySelector('.conv-detail');
    if (detailWrap) await renderDetail(detailWrap, currentId, false);
  }
  const listWrap = root.querySelector('.conv-list');
  if (listWrap) await renderList(listWrap, currentId, null, false);
}
