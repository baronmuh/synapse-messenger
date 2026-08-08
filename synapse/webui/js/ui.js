/* ==========================================================================
   Synapse — Reusable UI components (badges, avatars, cards, toasts,
   modals, empty states, skeletons, tooltips, table sorting).
   ========================================================================== */

import { icon } from './icons.js';
import { esc, avatarStyle, avatarInitial, timeAgo } from './format.js';

/* ---- Elements ------------------------------------------------------------ */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'aria') Object.entries(v).forEach(([ak, av]) => node.setAttribute(`aria-${ak}`, av));
    else node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    // The SVG strings produced by icon()/brandMark() are trusted internal
    // HTML (no user data flows through them).
    if (typeof child === 'string' && child.startsWith('<svg')) {
      node.insertAdjacentHTML('beforeend', child);
      continue;
    }
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

/* ---- Avatar --------------------------------------------------------------- */
export function avatar(username, size = 'md', extra = '') {
  const cls = size === 'lg' ? 'avatar avatar-lg' : size === 'sm' ? 'avatar avatar-sm' : 'avatar';
  return el('span', {
    class: `${cls} avatar-wrap`, style: `${avatarStyle(username)} ${extra}`, title: esc(username),
    'aria-hidden': 'true',
  }, el('span', { class: 'avatar-inner', style: 'display:inline-flex' }, avatarInitial(username)));
}

export function avatarWithStatus(username, status, size = 'md') {
  const dotColor = status === 'active' ? 'var(--color-accent)'
    : status === 'disabled' ? 'var(--color-ink-3)' : 'var(--color-link)';
  const wrap = avatar(username, size, 'position:relative');
  wrap.append(el('span', { class: 'status-dot', style: `background:${dotColor}` }));
  return wrap;
}

/* ---- Tooltip ----------------------------------------------------------------- */
/* Usage: tip(content, { below: true }) -> .tip-wrap wrapper; the
   hovered/focused element reveals the tooltip. Accessible: also on keyboard focus. */
export function tip(content, { below = false } = {}) {
  return el('span', { class: 'tip-wrap', tabindex: '-1', 'aria-hidden': 'true' },
    el('span', { class: `tip${below ? ' tip-below' : ''}` }, content));
}

/* ---- Badges ---------------------------------------------------------------- */
export function badge(text, tone = 'neutral', { dot = false, mono = false, title } = {}) {
  return el('span', {
    class: `badge badge-${tone}${mono ? ' badge-mono' : ''}`, title: title || '',
  }, dot ? el('span', { class: 'dot' }) : null, text);
}

export function statusBadge(agent) {
  if (agent.is_observer) return badge('Observer', 'info', { dot: true });
  if (agent.status === 'active') return badge('Active', 'accent', { dot: true });
  return badge('Inactive', 'neutral', { dot: true });
}

export function stateBadge(state) {
  const tones = { submitted: 'info', in_progress: 'accent', pending_approval: 'warn',
    completed: 'ok', failed: 'danger', canceled: 'neutral' };
  const labels = { submitted: 'Submitted', in_progress: 'In progress', pending_approval: 'Approval',
    completed: 'Completed', failed: 'Failed', canceled: 'Canceled' };
  return badge(labels[state] || state, tones[state] || 'neutral');
}

export function priorityBadge(priority) {
  const map = { low: ['Low', 'neutral'], normal: ['Normal', 'neutral'], high: ['High', 'warn'] };
  const [label, tone] = map[priority] || [priority, 'neutral'];
  return badge(label, tone);
}

/* ---- Card ------------------------------------------------------------------ */
export function card({ title, iconName = null, actions = null, body = null, foot = null, extraClass = '' }) {
  const header = (title || actions) ? el('div', { class: 'card-header' },
    title ? el('h2', { class: 'card-title' },
      iconName ? el('span', { class: 'card-icon', html: icon(iconName) }) : null, title) : null,
    actions || null,
  ) : null;
  const bodyNode = body ? el('div', { class: 'card-body' }, body) : null;
  const footNode = foot ? el('div', { class: 'card-foot' }, foot) : null;
  return el('section', { class: `card ${extraClass}` }, header, bodyNode, footNode);
}

/* ---- Toast ------------------------------------------------------------------- */
export function toast(type, message, { duration = 5000 } = {}) {
  const stack = document.getElementById('toasts');
  if (!stack) return;
  const node = el('div', { class: `toast toast-${type}`, role: 'status' },
    el('span', { class: 'toast-icon', html: icon(type === 'success' ? 'check' : type === 'error' ? 'error' : type === 'warn' ? 'warning' : 'info') }),
    el('span', { html: esc(message) }),
  );
  stack.append(node);
  const remove = () => { node.classList.add('leaving'); setTimeout(() => node.remove(), 200); };
  if (duration > 0) setTimeout(remove, duration);
  node.addEventListener('click', remove);
}

/* ---- Modal -------------------------------------------------------------------- */
export function openModal({ title, body, actions = [], onClose }) {
  const root = document.getElementById('modal-root');
  const prevFocus = document.activeElement;
  const close = () => {
    blanket.remove();
    document.removeEventListener('keydown', onKey);
    if (prevFocus && prevFocus.focus) prevFocus.focus();
    if (onClose) onClose();
  };
  const onKey = (e) => {
    if (e.key === 'Escape') { e.stopPropagation(); close(); }
    if (e.key === 'Tab') {
      const focusables = modal.querySelectorAll('button, [href], input, select, [tabindex]:not([tabindex="-1"])');
      if (!focusables.length) return;
      const first = focusables[0], last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
      else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
    }
  };
  const modal = el('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    el('div', { class: 'modal-header' },
      el('h3', { class: 'modal-title' }, title),
      el('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: close }, icon('close')),
    ),
    el('div', { class: 'modal-body' }, body),
    actions.length ? el('div', { class: 'modal-foot' }, ...actions) : null,
  );
  const blanket = el('div', { class: 'blanket', onclick: (e) => { if (e.target === blanket) close(); } }, modal);
  root.append(blanket);
  document.addEventListener('keydown', onKey);
  const focusables = modal.querySelectorAll('button, [href], input, select, [tabindex]:not([tabindex="-1"])');
  setTimeout(() => (focusables[0] || modal).focus(), 30);
  return { close, node: modal };
}

/* ---- Empty state ------------------------------------------------------------------- */
export function emptyState({ iconName = 'inbox', title, desc = '', action = null }) {
  return el('div', { class: 'empty' },
    el('div', { class: 'empty-icon', html: icon(iconName, 20) }),
    el('div', { class: 'empty-title' }, title),
    desc ? el('p', { class: 'empty-desc' }, desc) : null,
    action ? el('div', { class: 'empty-action' }, action) : null,
  );
}

/* ---- Skeleton ---------------------------------------------------------------------- */
export function skeletonRows(n = 6) {
  return el('div', { 'aria-hidden': 'true' }, ...Array.from({ length: n }, () => el('div', { class: 'skeleton skel-row' })));
}

export function skeletonCard() {
  return el('div', { class: 'skeleton skel-card', 'aria-hidden': 'true' });
}

/* ---- View header -------------------------------------------------------------------- */
export function pageHeader({ title, desc = '', actions = null, badgeEl = null }) {
  return el('header', { class: 'content-header' },
    el('div', { class: 'page-title' },
      el('h1', { html: esc(title) }),
      badgeEl ? el('div', { style: 'margin-top:4px' }, badgeEl) : null,
      desc ? el('p', { class: 'page-desc' }, desc) : null,
    ),
    actions ? el('div', { class: 'page-actions' }, actions) : null,
  );
}

/* ---- "Protected content" banner --------------------------------------------------------- */
export function protectedBanner() {
  return el('div', { class: 'banner banner-info banner-compact', role: 'note' },
    el('span', { class: 'banner-icon', html: icon('lock') }),
    el('span', { html: 'Protected content — <strong>non-disclosure</strong>: aggregated metadata only (who, what, when, state). '
      + 'Message and task content stays strictly between the agents.' }),
  );
}

/* ---- Flow line (a ⇄ b) ------------------------------------------------------------------ */
export function flowPair(a, b, { linked = true } = {}) {
  const make = (u) => el('span', {
    class: 'flow-name', title: esc(u),
  }, linked ? el('a', { href: `#/agents/${encodeURIComponent(u)}`, text: u }) : u);
  return el('span', { class: 'flow-pair' }, make(a), el('span', { class: 'flow-arrow', html: icon('arrowRight') }), make(b));
}

/* ---- Agent link in a cell ---------------------------------------------------------------- */
export function agentLink(username, { sub = null } = {}) {
  return el('div', { style: 'display:flex;flex-direction:column;gap:2px;min-width:0' },
    el('a', { href: `#/agents/${encodeURIComponent(username)}`, class: 'cell-main', text: username }),
    sub ? el('span', { class: 'cell-sub', html: sub }) : null,
  );
}

/* ---- Relative time ------------------------------------------------------------------------------- */
export function timeEl(iso, { mono = true } = {}) {
  if (!iso) return el('span', { text: '—' });
  return el('span', {
    class: mono ? 'mono' : '', title: iso,
  }, timeAgo(iso));
}

/* ==========================================================================
   Accessible table sorting (NN/g: visual sorting; WCAG: aria-sort).
   Usage: thSortable('label', key, state, onSort) -> <th> with button.
   state = { key, dir } current; onSort(key, dir) re-renders the table.
   ========================================================================== */
export function thSortable(label, key, state, onSort, { right = false } = {}) {
  const active = state.key === key;
  const dir = active ? state.dir : null;
  const iconName = dir === 'asc' ? 'sortAsc' : dir === 'desc' ? 'sortDesc' : 'sortAsc';
  // aria-sort belongs to the column (columnheader role), not the button.
  return el('th', {
    scope: 'col', class: 'sortable',
    'aria-sort': dir ? (dir === 'asc' ? 'ascending' : 'descending') : null,
    style: right ? 'text-align:right' : null,
  }, el('button', {
    class: 'th-btn',
    'aria-label': `Sort by ${label}${active ? `, ${dir === 'asc' ? 'ascending' : 'descending'}` : ''}`,
    onclick: () => {
      const nextDir = !active ? 'asc' : dir === 'asc' ? 'desc' : 'asc';
      onSort(key, nextDir);
    },
  }, label,
    // The sort icon only appears on the active column (NN/g: no
    // noise on inert columns; the indication stays accessible via
    // aria-sort and the button label).
    active ? el('span', { class: 'sort-icon', html: icon(iconName, 12) }) : null));
}

/* ---- Sparkline (mini trend bars) ------------------------------------------------ */
/* Data: array of numbers. Renders a mini histogram readable at a
   glance (preattentive length — NN/g). */
export function sparkline(values, { height = 40, tone = 'accent' } = {}) {
  const max = Math.max(1, ...values);
  const bars = values.map((v, i) => {
    const h = Math.max(2, Math.round((v / max) * height));
    return el('span', {
      class: 'mini-bar',
      style: `height:${h}px`,
      title: `${v}`,
      'aria-hidden': 'true',
    });
  });
  return el('div', { class: 'mini-bars', role: 'img', 'aria-label': `Trend: ${values.join(', ')}` }, ...bars);
}

/* ---- Simple pagination (cursor) ------------------------------------------------------------ */
export function pagination({ hasMore, onMore, count, label }) {
  return el('div', { class: 'table-foot' },
    el('span', { text: label }),
    hasMore ? el('button', { class: 'btn btn-ghost btn-sm', onclick: onMore }, 'Load more') : null,
  );
}
