/* ==========================================================================
   Synapse — Tasks view: state board (kanban) in metadata.
   Task titles and descriptions are protected content: only
   state, assignee, creator, priority and due date are exposed.
   ========================================================================== */

import { el, clear, badge, emptyState, pageHeader, protectedBanner,
         priorityBadge, avatarWithStatus } from '../ui.js';
import { esc, shortId, isDueSoon, timeAgo, stateLabel, STATE_LABELS } from '../format.js';
import { api } from '../api.js';

const COLUMNS = [
  ['submitted', 'Submitted', 'var(--color-link)'],
  ['in_progress', 'In progress', 'var(--color-accent)'],
  ['pending_approval', 'Approval', 'var(--color-warning)'],
  ['completed', 'Completed', 'var(--color-success)'],
  ['failed', 'Failed', 'var(--color-danger)'],
  ['canceled', 'Canceled', 'var(--color-rule-2)'],
];

let assigneeFilter = '';
let priorityFilter = '';

function dueEl(t) {
  if (!t.due_at) return el('span', { class: 'cell-sub', text: 'no due date' });
  const tone = isDueSoon(t.due_at);
  return el('span', { class: `due ${tone || ''}`, text: `due ${timeAgo(t.due_at)}` });
}

function taskCard(t) {
  const pending = t.state === 'pending_approval';
  return el('div', { class: 'kanban-card' },
    el('div', { class: 'kanban-card-top' },
      el('span', { class: 'kanban-task-id', title: t.task_id, text: `#${shortId(t.task_id)}` }),
      priorityBadge(t.priority),
    ),
    el('div', { class: 'kanban-card-meta' },
      avatarWithStatus(t.assignee_username, 'active', 'sm'),
      el('a', { href: `#/agents/${encodeURIComponent(t.assignee_username)}`, text: t.assignee_username }),
    ),
    el('div', { class: 'kanban-card-meta' },
      el('span', { text: `created by ${esc(t.creator_username)}` }),
    ),
    el('div', { class: 'kanban-card-meta' }, dueEl(t)),
    pending ? el('div', { class: 'kanban-card-meta' },
      badge(`approver: ${esc(t.approver_username || '—')}`, 'warn', { dot: true })) : null,
  );
}

function renderBoard(container) {
  const tasks = (api.snapshot?.tasks || []).filter(t => {
    if (assigneeFilter && t.assignee_username !== assigneeFilter) return false;
    if (priorityFilter && t.priority !== priorityFilter) return false;
    return true;
  });
  if (!tasks.length && !assigneeFilter && !priorityFilter) {
    container.append(emptyState({
      iconName: 'tasks', title: 'No tasks yet',
      desc: 'Tasks created by agents will appear here with their state, assignee and due date — without title or description, per non-disclosure.',
    }));
    return;
  }
  if (!tasks.length) {
    container.append(emptyState({
      iconName: 'filter', title: 'No tasks with these filters',
      desc: 'Try another assignee or another priority.',
      action: el('button', { class: 'btn btn-secondary', onclick: () => { assigneeFilter = ''; priorityFilter = ''; render(container); } }, 'Reset filters'),
    }));
    return;
  }
  const board = el('div', { class: 'kanban-scroll', tabindex: '0', 'aria-label': 'Task board by state (horizontal scroll)' },
    el('div', { class: 'kanban' }));
  for (const [state, label, color] of COLUMNS) {
    const colTasks = tasks.filter(t => t.state === state);
    const col = el('div', { class: 'kanban-col' },
      el('div', { class: 'kanban-col-head' },
        el('span', { class: 'dot', style: `background:${color}` }),
        el('span', { text: label }),
        el('span', { class: 'count', text: colTasks.length }),
      ),
      el('div', { class: 'kanban-body' },
        colTasks.length ? colTasks.map(taskCard) : el('span', { class: 'cell-sub', style: 'padding:8px;text-align:center', text: '—' }),
      ),
    );
    board.querySelector('.kanban').append(col);
  }
  container.append(board);
}

export function render(container) {
  const snap = api.snapshot;
  clear(container);
  container.append(
    pageHeader({
      title: 'Tasks',
      desc: "Coordination state: the organization's tasks, without title or description (protected content).",
    }),
    protectedBanner(),
  );
  if (!snap) {
    container.append(el('div', { style: 'margin-top:24px' }, el('div', { class: 'skeleton skel-card', style: 'height:320px' })));
    return;
  }
  const agents = (snap.agents || []).filter(a => !a.is_observer);
  const toolbar = el('div', { class: 'agents-toolbar', style: 'margin-top:24px' },
    el('div', { class: 'field' },
      el('label', { for: 't-aa' }, 'Assignee'),
      el('select', { id: 't-aa', class: 'select', onchange: (e) => { assigneeFilter = e.target.value; renderBoard(container); } },
        el('option', { value: '' }, 'All agents'),
        ...agents.map(a => el('option', { value: a.username, selected: assigneeFilter === a.username ? 'selected' : null }, a.username)),
      ),
    ),
    el('div', { class: 'field' },
      el('label', { for: 't-pr' }, 'Priority'),
      el('select', { id: 't-pr', class: 'select', onchange: (e) => { priorityFilter = e.target.value; renderBoard(container); } },
        el('option', { value: '' }, 'All'),
        ['low', 'normal', 'high'].map(p => el('option', { value: p, selected: priorityFilter === p ? 'selected' : null },
          { low: 'Low', normal: 'Normal', high: 'High' }[p])),
      ),
    ),
    el('span', { class: 'cell-sub' }, `${snap.tasks?.length || 0} tasks visible`),
  );
  container.append(toolbar);
  renderBoard(container);
}

export const refresh = render;
