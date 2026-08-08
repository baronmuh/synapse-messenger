/* ==========================================================================
   Synapse — Agent detail view: description, card, reputation, activity.
   ========================================================================== */

import { el, clear, badge, emptyState, pageHeader, avatarWithStatus, card,
         flowPair, timeEl, stateBadge, priorityBadge, skeletonRows } from '../ui.js';
import { icon } from '../icons.js';
import { esc, principalTypeLabel, shortId, isDueSoon, timeAgo, commandLabel, stateLabel } from '../format.js';
import { api } from '../api.js';

let detailCache = null;

async function loadDetail(username) {
  const res = await fetch(`/api/agents/${encodeURIComponent(username)}`, { cache: 'no-cache' });
  if (res.status === 404) return { notFound: true };
  if (!res.ok) throw new Error('cannot load the record');
  return { data: await res.json() };
}

function cardSection(cardData) {
  if (!cardData) {
    return card({
      title: 'Agent card', iconName: 'doc',
      body: el('p', { class: 'page-desc', text: 'This agent has not declared a card yet (set_agent_card).' }),
    });
  }
  const rows = [];
  if (cardData.domain) rows.push(['Domain', cardData.domain]);
  if (cardData.model) rows.push(['Model', cardData.model]);
  if (cardData.sla) rows.push(['Announced SLA', cardData.sla]);
  if (cardData.limits) rows.push(['Limits', cardData.limits]);
  if (cardData.estimated_cost) rows.push(['Estimated cost', cardData.estimated_cost]);
  const capChips = (cardData.capabilities || []).map(c => el('span', { class: 'chip', text: c }));
  const toolsChips = (cardData.tools || []).map(t => el('span', { class: 'chip', text: t }));
  const validated = cardData.validation_state === 'approved';
  return card({
    title: 'Agent card', iconName: 'doc',
    actions: badge(validated ? 'Validated' : 'Pending validation', validated ? 'ok' : 'warn', { dot: true }),
    body: el('div', { style: 'display:flex;flex-direction:column;gap:16px' },
      el('div', { style: 'display:flex;flex-direction:column;gap:8px' },
        el('span', { class: 'field-label' }, 'Capabilities'),
        capChips.length ? el('div', { class: 'cap-grid' }, ...capChips)
          : el('span', { class: 'cell-sub' }, 'no declared capability'),
      ),
      rows.length ? el('dl', { class: 'dl' }, ...rows.flatMap(([dt, dd]) => [el('dt', { text: dt }), el('dd', { text: dd })])) : null,
      toolsChips.length ? el('div', { style: 'display:flex;flex-direction:column;gap:8px' },
        el('span', { class: 'field-label' }, 'Tools'),
        el('div', { class: 'cap-grid' }, ...toolsChips)) : null,
    ),
  });
}

function reputationSection(rep) {
  const score = rep?.score ?? null;
  const qual = rep?.qualitative || null;
  const qualBadge = score === null ? badge('unknown', 'neutral')
    : qual === 'excellent' ? badge('excellent', 'ok', { dot: true })
      : qual === 'good' ? badge('good', 'ok', { dot: true })
        : qual === 'average' ? badge('average', 'warn', { dot: true })
          : badge('low', 'danger', { dot: true });
  const pct = score === null ? 0 : Math.round(score * 100);
  const barTone = score === null ? 'neutral'
    : qual === 'excellent' || qual === 'good' ? 'accent'
      : qual === 'average' ? 'warn' : 'danger';
  const barColor = { accent: 'var(--color-accent)', warn: 'var(--color-warning)',
    danger: 'var(--color-danger)', neutral: 'var(--color-rule-2)' }[barTone];
  return card({
    title: 'Reputation', iconName: 'pulse',
    body: el('div', { style: 'display:flex;flex-direction:column;gap:12px' },
      el('div', { style: 'display:flex;align-items:center;gap:20px' },
        el('div', { style: 'text-align:center' },
          el('div', { class: 'kpi-value', text: score === null ? '—' : pct }),
          el('div', { class: 'cell-sub', text: '/ 100' }),
        ),
        el('div', { style: 'display:flex;flex-direction:column;gap:6px' },
          qualBadge,
          el('span', { class: 'cell-sub', text: rep?.note ? `"${rep.note}"` : 'Note computed by the server from completed tasks.' }),
        ),
      ),
      // Score bar: preattentive length (NN/g), never color alone.
      el('div', { class: 'bar-track', style: 'height:10px;background:var(--color-paper-2);border:1px solid var(--color-rule);border-radius:var(--radius-full);overflow:hidden',
          role: 'img', 'aria-label': score === null ? 'Unknown reputation' : `Reputation score: ${pct} out of 100` },
        el('div', { class: 'bar-fill', style: `width:${pct}%;background:${barColor};height:100%;border-radius:var(--radius-full)` })),
    ),
  });
}

function conversationsSection(username) {
  const convs = api.agentConversations(username);
  if (!convs.length) {
    return card({ title: 'Conversations', iconName: 'message',
      body: el('p', { class: 'page-desc', text: 'No internal exchanges for this agent.' }) });
  }
  return card({
    title: 'Conversations', iconName: 'message',
    body: el('div', { class: 'list-plain' }, ...convs.map(c => {
      const other = c.a === username ? c.b : c.a;
      return el('div', { class: 'list-row' },
        el('div', { style: 'flex:1;min-width:0' },
          el('div', { class: 'row-title' }, flowPair(c.a, c.b)),
          el('div', { class: 'row-sub' }, `${c.message_count} messages · last ${timeAgo(c.last_at)}`),
        ),
        (c.unread_count || 0) > 0 ? badge(`${c.unread_count} unread${c.unread_count > 1 ? 's' : ''}`, 'warn', { dot: true }) : badge('read', 'neutral'),
      );
    })),
  });
}

function tasksSection(username) {
  const tasks = api.agentTasks(username);
  if (!tasks.length) {
    return card({ title: 'Tasks', iconName: 'tasks',
      body: el('p', { class: 'page-desc', text: 'No task (created or assigned) for this agent.' }) });
  }
  return card({
    title: 'Tasks', iconName: 'tasks',
    body: el('div', { class: 'list-plain' }, ...tasks.map(t => {
      const overdue = isDueSoon(t.due_at);
      return el('a', { class: 'list-row', href: '#/tasks', style: 'text-decoration:none;color:inherit' },
        el('div', { style: 'flex:1;min-width:0' },
          el('div', { class: 'row-title', style: 'display:flex;align-items:center;gap:8px' },
            el('span', { class: 'mono', text: shortId(t.task_id) }),
            stateBadge(t.state),
          ),
          el('div', { class: 'row-sub', html:
            `assigned to <strong>${esc(t.assignee_username)}</strong>${t.due_at ? ` · due <span class="due ${overdue || ''}">${timeAgo(t.due_at)}</span>` : ''}` }),
        ),
        priorityBadge(t.priority),
      );
    })),
  });
}

function activitySection(username) {
  const entries = (api.snapshot?.recent_audit || []).filter(e => e.actor_username === username).slice(0, 8);
  if (!entries.length) {
    return card({ title: 'Recent activity', iconName: 'activity',
      body: el('p', { class: 'page-desc', text: 'No recent actions from this agent.' }) });
  }
  return card({
    title: 'Recent activity', iconName: 'activity',
    body: el('div', { class: 'list-plain' }, ...entries.map(e =>
      el('div', { class: 'list-row' },
        el('div', { style: 'flex:1;min-width:0' },
          el('div', { class: 'row-title', style: 'display:flex;align-items:center;gap:8px' },
            el('span', { class: 'audit-cmd', text: e.command })),
          el('div', { class: 'row-sub', text: commandLabel(e.command) }),
        ),
        timeEl(e.at),
      ))),
  });
}

export async function render(container, params) {
  const username = params.username;
  clear(container);
  container.append(pageHeader({ title: username, desc: 'Loading the record…' }),
    el('div', { class: 'grid grid-2', style: 'margin-top:16px' },
      el('div', { class: 'skeleton skel-card', style: 'height:140px' }),
      el('div', { class: 'skeleton skel-card', style: 'height:140px' })));

  let detail;
  try {
    detail = await loadDetail(username);
  } catch (e) {
    clear(container);
    container.append(pageHeader({ title: username }),
      emptyState({ iconName: 'error', title: 'Card unavailable',
        desc: e.message + '. Check that the server is reachable, then retry.',
        action: el('button', { class: 'btn btn-secondary', onclick: () => render(container, params) }, 'Retry') }));
    return;
  }
  if (detail.notFound) {
    clear(container);
    container.append(pageHeader({ title: username }),
      emptyState({ iconName: 'search', title: 'Agent not found',
        desc: 'This account does not exist or is not visible from your account.',
        action: el('a', { class: 'btn btn-secondary', href: '#/agents' }, 'Back to the directory') }));
    return;
  }
  detailCache = detail.data;
  renderDetail(container, username, detail.data);
}

export function renderDetail(container, username, detail) {
  clear(container);
  const snapAgent = api.agentByUsername(username);
  const dept = api.departmentOf(username);

  const badgesRow = el('div', { style: 'display:flex;gap:8px;flex-wrap:wrap' },
    snapAgent ? (snapAgent.status === 'active' ? badge('Active', 'accent', { dot: true }) : badge('Inactive', 'neutral', { dot: true })) : null,
    snapAgent?.is_observer ? badge('Observer', 'info') : null,
    badge(principalTypeLabel(snapAgent?.principal_type), 'neutral'),
    dept ? badge(`${dept.department}${dept.role ? ' · ' + dept.role : ''}`, 'info') : null,
  );

  container.append(
    pageHeader({ title: username, actions: el('a', { class: 'btn btn-secondary btn-sm', href: '#/agents' }, '← Directory') }),
    el('div', { class: 'agent-hero' },
      avatarWithStatus(username, snapAgent?.status || 'active', 'lg'),
      el('div', { class: 'agent-hero-info' },
        el('div', { class: 'agent-hero-name' }, username, badgesRow),
        el('p', { class: 'agent-hero-desc' }, detail.description || 'No description.'),
        el('div', { class: 'cell-sub' },
          `organization ${esc(detail.organization_name)}${dept ? '' : ' · no department'}`),
      ),
    ),
  );

  container.append(el('div', { class: 'grid grid-main-side', style: 'margin-top:24px' },
    el('div', { style: 'display:flex;flex-direction:column;gap:24px' },
      cardSection(detail.card),
      reputationSection(detail.reputation),
    ),
    el('div', { style: 'display:flex;flex-direction:column;gap:24px' },
      conversationsSection(username),
      tasksSection(username),
      activitySection(username),
    ),
  ));
}

export const refresh = (container, params) => {
  if (detailCache) renderDetail(container, params.username, detailCache);
};
