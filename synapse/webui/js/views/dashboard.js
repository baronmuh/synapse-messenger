/* ==========================================================================
   Synapse — Dashboard view: the organization state on one page.
   Inverted pyramid (UX Pilot/NN/g): KPIs → what needs attention →
   trends → details. No gratuitous statistics: every block points to
   a decision.
   ========================================================================== */

import { el, clear, card, badge, emptyState, pageHeader, avatarWithStatus,
         flowPair, timeEl, protectedBanner } from '../ui.js';
import { icon } from '../icons.js';
import { esc, stateLabel, shortId, isDueSoon, timeAgo, commandLabel, STATE_TONES } from '../format.js';
import { api } from '../api.js';

function kpi(label, value, sub, tone) {
  return el('div', { class: 'kpi', 'data-tone': tone || 'neutral' },
    el('span', { class: 'kpi-label' }, label),
    el('span', { class: 'kpi-value' }, value),
    sub ? el('span', { class: 'kpi-sub' }, sub) : null,
  );
}

function attentionItems() {
  const items = [];
  const overdue = api.overdueTasks(3);
  for (const t of overdue) {
    items.push({
      tone: 'danger', iconName: 'clock', iconClass: 'att-danger',
      title: `Overdue task · ${stateLabel(t.state)}`,
      desc: `assigned to ${esc(t.assignee_username)} · due ${timeAgo(t.due_at)}`,
      href: '#/tasks',
    });
  }
  const pending = (api.snapshot?.tasks_by_state || {}).pending_approval || 0;
  if (pending > 0) {
    items.push({
      tone: 'warn', iconName: 'shield', iconClass: 'att-warn',
      title: `${pending} task${pending > 1 ? 's' : ''} pending approval`,
      desc: 'an agent awaits a decision',
      href: '#/tasks',
    });
  }
  for (const c of api.conversationsNeedingReply(3)) {
    items.push({
      tone: 'warn', iconName: 'message', iconClass: 'att-info',
      title: `${c.unread_count} message${c.unread_count > 1 ? 's' : ''} unread${c.unread_count > 1 ? 's' : ''}`,
      desc: `${esc(c.a)} ⇄ ${esc(c.b)} · last exchange ${timeAgo(c.last_at)}`,
      href: '#/communications',
    });
  }
  const inactive = (api.snapshot?.agents || []).filter(a => a.status !== 'active' && !a.is_observer);
  if (inactive.length > 0) {
    items.push({
      tone: 'info', iconName: 'user', iconClass: 'att-ok',
      title: `${inactive.length} agent${inactive.length > 1 ? 's' : ''} inactive${inactive.length > 1 ? 's' : ''}`,
      desc: inactive.slice(0, 3).map(a => esc(a.username)).join(', '),
      href: '#/agents',
    });
  }
  return items;
}

function renderAttention() {
  const items = attentionItems();
  if (items.length === 0) {
    return el('div', { class: 'banner banner-success', role: 'status' },
      el('span', { class: 'banner-icon', html: icon('check') }),
      el('span', { html: 'All green: no overdue task, no pending approval, '
        + 'no unread message, no inactive agent.' }),
    );
  }
  const list = el('div', { class: 'attention-list' }, ...items.map(it =>
    el('a', { class: 'attention-item', href: it.href, style: 'text-decoration:none;color:inherit' },
      el('span', { class: `att-icon ${it.iconClass}`, html: icon(it.iconName) }),
      el('div', { style: 'flex:1;min-width:0' },
        el('div', { class: 'row-title', text: it.title }),
        el('div', { class: 'row-sub', html: it.desc }),
      ),
      el('span', { class: 'cell-sub', html: icon('chevron', 14) }),
    ),
  ));
  return list;
}

function renderFlow(limit = 5) {
  const convs = (api.snapshot?.conversations || []).slice(0, limit);
  if (convs.length === 0) {
    return emptyState({
      iconName: 'message', title: 'No internal exchanges yet',
      desc: 'Agent communication flows will appear here after the first exchanged message (metadata only).',
    });
  }
  return el('div', { class: 'list-plain' }, ...convs.map(c =>
    el('div', { class: 'list-row', style: 'border-radius:0' },
      avatarWithStatus(c.a, 'active', 'sm'),
      el('div', { style: 'flex:1;min-width:0' },
        el('div', { class: 'row-title' }, flowPair(c.a, c.b)),
        el('div', { class: 'row-sub' },
          `${c.message_count} message${c.message_count > 1 ? 's' : ''} · last ${timeAgo(c.last_at)}`),
      ),
      (c.unread_count || 0) > 0
        ? badge(`${c.unread_count} unread${c.unread_count > 1 ? 's' : ''}`, 'warn', { dot: true })
        : badge('read', 'neutral'),
    ),
  ));
}

function renderTaskBars() {
  const counts = api.taskCounts();
  const order = [
    ['submitted', 'Soumises', 'info'],
    ['in_progress', 'In progress', 'accent'],
    ['pending_approval', 'Approbation', 'warn'],
    ['completed', 'Completed', 'ok'],
    ['failed', 'Failed', 'danger'],
    ['canceled', 'Canceled', 'neutral'],
  ];
  const max = Math.max(1, ...order.map(([s]) => counts[s] || 0));
  const bars = order.map(([s, label, tone]) => {
    const n = counts[s] || 0;
    const color = { info: 'var(--color-link)', accent: 'var(--color-accent)',
      warn: 'var(--color-warning)', ok: 'var(--color-success)',
      danger: 'var(--color-danger)', neutral: 'var(--color-rule-2)' }[tone];
    return el('div', { class: 'bar-row' },
      el('span', { class: 'bar-label' }, label),
      el('div', { class: 'bar-track' }, el('div', { class: 'bar-fill', style: `width:${(n / max) * 100}%;background:${color}` })),
      el('span', { class: 'bar-count' }, n),
    );
  });
  return el('div', { class: 'bars' }, ...bars);
}

function renderDepartments() {
  const depts = api.snapshot?.departments || [];
  if (depts.length === 0) {
    return el('p', { class: 'page-desc', html: 'Flat organization: no declared department. '
      + 'This is a valid model (\"small first\") — structure is optional.' });
  }
  return el('div', { class: 'list-plain' }, ...depts.map(d =>
    el('div', { class: 'list-row', style: 'border-radius:0' },
      el('span', { class: 'dept-icon', html: icon('organisation') }),
      el('div', { style: 'flex:1;min-width:0' },
        el('div', { class: 'row-title', text: d.department_name }),
        el('div', { class: 'row-sub', text: `${d.members.length} member${d.members.length > 1 ? 's' : ''}` }),
      ),
      el('a', { href: '#/organization', class: 'cell-sub' }, 'voir'),
    ),
  ));
}

function renderAudit(limit = 8) {
  const entries = (api.snapshot?.recent_audit || []).slice(0, limit);
  if (entries.length === 0) {
    return el('p', { class: 'page-desc', text: 'No recent activity.' });
  }
  return el('div', { class: 'list-plain' }, ...entries.map(e =>
    el('div', { class: 'list-row', style: 'border-radius:0' },
      el('div', { style: 'flex:1;min-width:0' },
        el('div', { class: 'row-title', style: 'display:flex;align-items:center;gap:8px' },
          el('a', { href: `#/agents/${encodeURIComponent(e.actor_username)}`, text: e.actor_username }),
          el('span', { class: 'audit-cmd', text: e.command }),
        ),
        el('div', { class: 'row-sub', text: commandLabel(e.command) }),
      ),
      timeEl(e.at),
    ),
  ));
}

export function render(container) {
  const snap = api.snapshot;
  const counts = api.taskCounts();
  const statuses = api.agentsByStatus();
  const unread = api.unreadTotal();
  const messages = snap?.messages_last_hour ?? 0;
  const totalAgents = snap?.agents?.length ?? 0;
  const activeRate = totalAgents ? Math.round((statuses.active / totalAgents) * 100) : 0;

  clear(container);
  if (!snap) {
    container.append(pageHeader({ title: 'Dashboard', desc: 'Loading the organization state…' }),
      el('div', { class: 'grid grid-4' },
        el('div', { class: 'skeleton skel-card' }), el('div', { class: 'skeleton skel-card' }),
        el('div', { class: 'skeleton skel-card' }), el('div', { class: 'skeleton skel-card' })),
      el('div', { class: 'grid grid-main-side', style: 'margin-top:24px' },
        el('div', { class: 'skeleton skel-card', style: 'height:300px' }),
        el('div', { class: 'skeleton skel-card', style: 'height:300px' })));
    return;
  }

  container.append(
    pageHeader({
      title: snap.organization_name,
      desc: 'Real-time organization state — data refreshes automatically (5s). Exchange content is read in the Conversations view.',
      badgeEl: badge('human account', 'accent', { dot: true }),
      actions: el('button', { class: 'btn btn-ghost btn-sm', onclick: () => api.poll(), 'aria-label': 'Refresh' }, icon('refresh', 14), 'Actualiser'),
    }),
  );

  container.append(
    el('div', { class: 'dash-kpis' },
      kpi('Agents actifs', `${statuses.active}`, `${totalAgents} account${totalAgents > 1 ? 's' : ''} · ${activeRate} % active`, 'accent'),
      kpi('Active tasks', counts.active, `${counts.pending_approval || 0} pending approval`, counts.pending_approval ? 'warn' : 'neutral'),
      kpi('Unread messages', unread, 'across all conversations', unread ? 'warn' : 'neutral'),
      kpi('Messages / heure', messages, 'volume de communication', 'info'),
    ),
  );

  const attentionCard = card({
    title: 'What needs your attention', iconName: 'alert',
    body: renderAttention(),
    foot: 'Organization metadata — exchange content is read in the Conversations view.',
  });

  const flowCard = card({
    title: 'Flux de communication', iconName: 'message',
    body: renderFlow(),
    foot: el('a', { href: '#/communications' }, 'All exchanges →'),
  });

  const tasksCard = card({ title: 'Tasks by state', iconName: 'tasks', body: renderTaskBars(), foot: el('a', { href: '#/tasks' }, 'Detailed view →') });
  const deptCard = card({ title: 'Departments', iconName: 'organisation', body: renderDepartments(), foot: el('a', { href: '#/organization' }, 'Full structure →') });
  const auditCard = card({ title: 'Recent activity', iconName: 'activity', body: renderAudit(), foot: el('a', { href: '#/activity' }, 'Full journal →') });

  container.append(el('div', { style: 'margin-top:24px' }, attentionCard));
  container.append(el('div', { class: 'grid grid-main-side', style: 'margin-top:24px' },
    el('div', { style: 'display:flex;flex-direction:column;gap:24px' }, flowCard, tasksCard),
    el('div', { style: 'display:flex;flex-direction:column;gap:24px' }, deptCard, auditCard),
  ));
}

export const refresh = render;
