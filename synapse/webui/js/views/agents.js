/* ==========================================================================
   Synapse — Agents view: directory + search + filters + sort.
   ========================================================================== */

import { el, clear, badge, emptyState, pageHeader, avatarWithStatus, openModal, toast } from '../ui.js';
import { esc, principalTypeLabel } from '../format.js';
import { api } from '../api.js';

let filterStatus = 'all';   // all | active | inactive
let filterType = 'all';     // all | agent | observer | human
let query = '';
let sortBy = 'name';        // name | status | type

function matches(a, q) {
  if (!q) return true;
  const hay = `${a.username} ${a.description || ''}`.toLowerCase();
  return q.toLowerCase().split(/\s+/).every(part => hay.includes(part));
}

function sortedAgents(agents) {
  const out = [...agents];
  const cmp = {
    name: (x, y) => x.username.localeCompare(y.username),
    status: (x, y) => String(x.status).localeCompare(String(y.status)) || x.username.localeCompare(y.username),
    type: (x, y) => String(x.principal_type).localeCompare(String(y.principal_type)) || x.username.localeCompare(y.username),
  }[sortBy] || ((x, y) => x.username.localeCompare(y.username));
  out.sort(cmp);
  return out;
}

function agentCardEl(a, dept) {
  const deptInfo = dept ? `${dept.department}${dept.role ? ' · ' + dept.role : ''}` : 'no department';
  return el('a', {
    class: 'agent-card', href: `#/agents/${encodeURIComponent(a.username)}`,
    'aria-label': `Record of ${a.username}`,
  },
    el('div', { class: 'agent-card-head' },
      avatarWithStatus(a.username, a.status),
      el('div', { style: 'flex:1;min-width:0' },
        el('div', { class: 'agent-card-name', text: a.username }),
        el('div', { class: 'cell-sub', text: principalTypeLabel(a.principal_type) }),
      ),
      a.status === 'active' ? badge('Active', 'accent', { dot: true })
        : badge('Inactive', 'neutral', { dot: true }),
    ),
    el('p', { class: 'agent-card-desc' }, a.description || 'No description.'),
    el('div', { class: 'agent-card-foot' },
      a.is_observer ? badge('Observer', 'info') : null,
      badge(deptInfo, 'neutral'),
    ),
  );
}

function renderGrid(results) {
  const snap = api.snapshot;
  const deptMap = new Map();
  for (const d of snap?.departments || []) {
    for (const m of d.members) deptMap.set(m.username, { department: d.department_name, role: m.role });
  }
  clear(results);
  let agents = (snap?.agents || []).filter(a => matches(a, query)).filter(a => {
    if (filterStatus === 'active' && a.status !== 'active') return false;
    if (filterStatus === 'inactive' && a.status === 'active') return false;
    if (filterType === 'observer' && !a.is_observer) return false;
    if (filterType === 'agent' && (a.is_observer || a.principal_type === 'human')) return false;
    if (filterType === 'human' && a.principal_type !== 'human') return false;
    return true;
  });
  agents = sortedAgents(agents);

  if (!snap || snap.agents.length === 0) {
    results.append(emptyState({
      iconName: 'agents', title: 'No agent in this organization',
      desc: 'Create your first agent with the synapse CLI (create_agent): it will appear here as soon as it exists, with its status and description.',
      action: el('button', { class: 'btn btn-primary', onclick: () => api.poll() }, 'Check again'),
    }));
    return;
  }
  if (agents.length === 0) {
    results.append(emptyState({
      iconName: 'search', title: 'No results',
      desc: 'No agent matches these filters. Try broadening the search or changing the status filter.',
      action: el('button', { class: 'btn btn-secondary', onclick: () => { query = ''; filterStatus = 'all'; filterType = 'all'; renderGrid(results); } }, 'Reset filters'),
    }));
    return;
  }
  results.append(el('div', { class: 'agents-grid' }, ...agents.map(a => agentCardEl(a, deptMap.get(a.username)))));
}

export function render(container) {
  const snap = api.snapshot;
  clear(container);
  container.append(pageHeader({
    title: 'Agents',
    desc: 'Organization directory — status, description and role of each account. Open a record for the capability card and reputation.',
    badgeEl: snap ? badge(`${snap.agents.length} account${snap.agents.length > 1 ? 's' : ''}`, 'neutral') : null,
  }));

  if (!snap) {
    container.append(el('div', { class: 'agents-grid' },
      ...Array.from({ length: 6 }, () => el('div', { class: 'skeleton skel-card' }))));
    return;
  }

  const toolbar = el('div', { class: 'agents-toolbar' },
    el('div', { class: 'search-field' },
      el('span', { class: 'search-icon', html: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20.5 20.5-4.6-4.6"/></svg>' }),
      el('input', {
        class: 'input', type: 'search', placeholder: 'Search an agent, a description…',
        'aria-label': 'Search an agent',
        value: query,
        oninput: (e) => { query = e.target.value; renderGrid(results); },
      }),
    ),
    el('div', { class: 'toolbar-segs' },
      el('div', { class: 'seg', role: 'group', 'aria-label': 'Filter by status' },
        [['all', 'All'], ['active', 'Active'], ['inactive', 'Inactive']].map(([v, label]) =>
          el('button', { 'aria-pressed': filterStatus === v ? 'true' : 'false', onclick: () => { filterStatus = v; renderGrid(results); } }, label)),
      ),
      el('div', { class: 'seg', role: 'group', 'aria-label': 'Filter by type' },
        [['all', 'All'], ['agent', 'Agents'], ['human', 'Humans'], ['observer', 'Observers']].map(([v, label]) =>
          el('button', { 'aria-pressed': filterType === v ? 'true' : 'false', onclick: () => { filterType = v; renderGrid(results); } }, label)),
      ),
      el('div', { class: 'seg', role: 'group', 'aria-label': 'Sort' },
        [['name', 'Name'], ['status', 'Status'], ['type', 'Type']].map(([v, label]) =>
          el('button', { 'aria-pressed': sortBy === v ? 'true' : 'false', onclick: () => { sortBy = v; renderGrid(results); } }, label)),
      ),
    ),
  );
  container.append(toolbar);
  const results = el('div', { class: 'agents-results' });
  container.append(results);
  renderGrid(results);
  managementPanel(container, snap);
}

export const refresh = render;

/* ==========================================================================
  Agent management (SPEC-WEB §4): creation, deactivation/reactivation,
  description modification. The organization powers of the session
  human powers are exercised by the web server.
  ========================================================================== */

function createAgentModal() {
  const form = el('form', { class: 'form-stack', novalidate: true });
  const username = el('input', { class: 'input', type: 'text', required: true,
    placeholder: 'e.g. agent_d', spellcheck: 'false', 'aria-label': "Username (3-64 [a-z0-9_-])" });
  const password = el('input', { class: 'input', type: 'password', required: true,
    minlength: 12, placeholder: 'Password (>= 12 characters)',
    'aria-label': 'New agent password' });
  const description = el('input', { class: 'input', type: 'text', required: true,
    placeholder: 'Short description (role, capabilities)', 'aria-label': 'Description' });
  const error = el('div', { class: 'form-error', role: 'alert', hidden: true });
  form.append(
    el('label', { class: 'field-label' }, 'Username'), username,
    el('label', { class: 'field-label' }, 'Password'), password,
    el('label', { class: 'field-label' }, 'Description'), description,
    error,
  );
  const submit = el('button', { type: 'submit', class: 'btn btn-primary', text: 'Create agent' });
  openModal({
    title: 'Create an agent',
    body: el('div', null,
      el('p', { class: 'modal-sub' }, 'The account is created active, attached to your organization.'),
      form),
    actions: [submit],
  });
  form.onsubmit = async (e) => {
    e.preventDefault();
    error.hidden = true;
    submit.disabled = true;
    try {
      await api.createAgent(username.value.trim(), password.value, description.value.trim());
      document.querySelector('.blanket')?.remove();
      toast('ok', `Agent ${username.value.trim()} created`);
      api.poll();
    } catch (err) {
      error.textContent = err.message || 'Creation failed';
      error.hidden = false;
      submit.disabled = false;
    }
  };
}

function editDescriptionModal(a) {
  const form = el('form', { class: 'form-stack', novalidate: true });
  const input = el('input', { class: 'input', type: 'text', value: a.description || '',
    'aria-label': 'New description' });
  const error = el('div', { class: 'form-error', role: 'alert', hidden: true });
  form.append(input, error);
  const submit = el('button', { type: 'submit', class: 'btn btn-primary', text: 'Save' });
  openModal({
    title: `Edit the description of ${a.username}`,
    body: form,
    actions: [submit],
  });
  form.onsubmit = async (e) => {
    e.preventDefault();
    error.hidden = true;
    submit.disabled = true;
    try {
      await api.changeAgentDescription(a.username, input.value.trim());
      document.querySelector('.blanket')?.remove();
      toast('ok', 'Description updated');
      api.poll();
    } catch (err) {
      error.textContent = err.message || 'Edit failed';
      error.hidden = false;
      submit.disabled = false;
    }
  };
}

async function toggleAgent(a, btn) {
  btn.disabled = true;
  try {
    if (a.status === 'active') {
      await api.deactivateAgent(a.username);
      toast('ok', `${a.username} deactivated — access cut off, data kept`);
    } else {
      await api.reactivateAgent(a.username);
      toast('ok', `${a.username} reactivated`);
    }
    api.poll();
  } catch (err) {
    toast('error', err.message || 'Operation failed');
    btn.disabled = false;
  }
}

function managementPanel(container, snap) {
  const human = api.session?.human_username;
  const manageables = (snap.agents || []).filter(a => a.principal_type !== 'human');
  const panel = el('section', { class: 'card manage-panel', 'aria-label': 'Agent management' },
    el('div', { class: 'manage-head' },
      el('div', null,
        el('h2', { class: 'card-title' }, 'Agent management'),
        el('p', { class: 'cell-sub' },
          'Creation, deactivation and description — organization powers are exercised by the human session.'),
      ),
      el('button', { class: 'btn btn-secondary', onclick: createAgentModal },
        el('span', { style: 'display:inline-flex', html: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>' }),
        'Create an agent'),
    ),
    el('div', { class: 'manage-list' },
      manageables.length === 0
        ? el('p', { class: 'cell-sub', text: 'No agent to manage for now.' })
        : manageables.map(a => el('div', { class: 'manage-row' },
          avatarWithStatus(a.username, a.status, 'sm'),
          el('div', { style: 'flex:1;min-width:0' },
            el('div', { class: 'manage-row-name', text: a.username }),
            el('div', { class: 'cell-sub' },
              a.status === 'active' ? badge('Active', 'accent', { dot: true })
                : badge('Deactivated', 'neutral', { dot: true }),
              el('span', { text: a.is_observer ? 'observer' : 'agent' }),
            ),
          ),
          a.username === human ? badge('you', 'info') : null,
          el('button', {
            class: 'btn btn-ghost btn-sm', type: 'button',
            onclick: () => editDescriptionModal(a),
          }, 'Description'),
          el('button', {
            class: `btn btn-sm ${a.status === 'active' ? 'btn-danger' : 'btn-secondary'}`,
            type: 'button',
            onclick: (e) => toggleAgent(a, e.currentTarget),
          }, a.status === 'active' ? 'Deactivate' : 'Reactivate'),
        )),
    ),
  );
  container.append(panel);
}
