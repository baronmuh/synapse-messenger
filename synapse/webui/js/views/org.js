/* ==========================================================================
   Synapse — Organization view: structure, departments, roles, policies.
   Two representations: the live org chart (graphical view by default)
   and the department cards (detailed view).
   ========================================================================== */

import { el, clear, badge, emptyState, pageHeader, avatarWithStatus, openModal, toast } from '../ui.js';
import { icon } from '../icons.js';
import { esc, principalTypeLabel } from '../format.js';
import { api } from '../api.js';

function policyBadge(org) {
  if (!org) return null;
  const incoming = org.allow_incoming_external;
  const outgoing = org.allow_outgoing_external;
  return el('div', { style: 'display:flex;gap:8px;flex-wrap:wrap' },
    badge(incoming ? 'external incoming allowed' : 'external incoming blocked', incoming ? 'ok' : 'neutral', { dot: true }),
    badge(outgoing ? 'external outgoing allowed' : 'external outgoing blocked', outgoing ? 'ok' : 'neutral', { dot: true }),
  );
}

function roleBadge(role) {
  const map = { manager: ['manager', 'info'], employee: ['employee', 'neutral'], rh: ['HR', 'accent'] };
  const [label, tone] = map[role] || [role, 'neutral'];
  return badge(label, tone, { mono: true });
}

function agentNode(m, tone) {
  /* Org chart node: avatar + link + role. The link is real (keyboard,
     screen readers); column layout and connectors are
     purely visual. */
  return el('div', { class: 'org-chart-node' },
    avatarWithStatus(m.username, 'active', 'sm'),
    el('a', { href: `#/agents/${encodeURIComponent(m.username)}`, class: 'org-chart-link', text: m.username }),
    roleBadge(m.role),
  );
}

function orgChartOrphans(snap) {
  const inDept = new Set();
  for (const d of snap.departments || []) for (const m of d.members) inDept.add(m.username);
  return (snap.agents || []).filter(a => !inDept.has(a.username) && !a.is_observer);
}

function renderOrgChart(container) {
  const snap = api.snapshot;
  const depts = snap?.departments || [];
  const chart = el('div', {
    class: 'org-chart',
    'aria-label': `Org chart of ${snap.organization_name}`,
  });
  // Root: the organization.
  chart.append(el('div', { class: 'org-chart-root' },
    el('span', { class: 'dept-icon', html: icon('organisation') }),
    el('span', { text: snap.organization_name }),
  ));
  const grid = el('div', { class: 'org-chart-grid' });
  for (const d of depts) {
    const col = el('section', { class: 'org-chart-col', 'aria-label': `Department ${d.department_name}` });
    col.append(el('div', { class: 'org-chart-dept', text: d.department_name }));
    const managers = d.members.filter(m => m.role === 'manager');
    const others = d.members.filter(m => m.role !== 'manager');
    for (const m of managers) col.append(agentNode(m, 'manager'));
    if (managers.length) col.append(el('div', { class: 'org-chart-connector', 'aria-hidden': 'true' }));
    for (const m of others) col.append(agentNode(m, m.role));
    grid.append(col);
  }
  const orphans = orgChartOrphans(snap);
  if (orphans.length) {
    const col = el('section', { class: 'org-chart-col', 'aria-label': 'No department' });
    col.append(el('div', { class: 'org-chart-dept', text: 'No department' }));
    for (const a of orphans) {
      col.append(el('div', { class: 'org-chart-node' },
        avatarWithStatus(a.username, a.status, 'sm'),
        el('a', { href: `#/agents/${encodeURIComponent(a.username)}`, class: 'org-chart-link', text: a.username }),
        el('span', { class: 'cell-sub', text: principalTypeLabel(a.principal_type) }),
      ));
    }
    grid.append(col);
  }
  chart.append(grid);
  container.append(chart);
}

function renderDepartments(container) {
  const snap = api.snapshot;
  const depts = snap?.departments || [];
  const inDept = new Set();
  if (depts.length === 0) {
    container.append(
      el('div', { class: 'banner banner-info', role: 'note', style: 'margin-bottom:24px' },
        el('span', { class: 'banner-icon', html: icon('info') }),
        el('span', { html: 'Flat <strong>organization</strong>: no declared department. This is a perfectly valid model '
          + '(\"small first\") — hierarchy is an organizational option, never a precondition.' }),
      ),
      emptyState({
        iconName: 'organisation', title: 'No hierarchical structure',
        desc: "Departments (create_department, set_agent_department) let you group agents by team or domain. The view will become the organization's live org chart.",
      }),
    );
    return;
  }
  const grid = el('div', { class: 'grid grid-2' });
  for (const d of depts) {
    for (const m of d.members) inDept.add(m.username);
    const members = d.members.map(m => el('div', { class: 'dept-member' },
      avatarWithStatus(m.username, 'active', 'sm'),
      el('a', { href: `#/agents/${encodeURIComponent(m.username)}`, style: 'font-weight:500', text: m.username }),
      roleBadge(m.role),
    ));
    grid.append(el('div', { class: 'dept-card' },
      el('div', { class: 'dept-head' },
        el('span', { class: 'dept-icon', html: icon('organisation') }),
        el('span', { class: 'dept-name', text: d.department_name }),
        el('span', { class: 'dept-count', text: `${d.members.length} member${d.members.length > 1 ? 's' : ''}` }),
      ),
      el('div', { class: 'dept-body' }, ...members),
    ));
  }
  const orphans = (snap?.agents || []).filter(a => !inDept.has(a.username) && !a.is_observer);
  if (orphans.length) {
    grid.append(el('div', { class: 'dept-card' },
      el('div', { class: 'dept-head' },
        el('span', { class: 'dept-icon', html: icon('user') }),
        el('span', { class: 'dept-name', text: 'No department' }),
        el('span', { class: 'dept-count', text: `${orphans.length} agent${orphans.length > 1 ? 's' : ''}` }),
      ),
      el('div', { class: 'dept-body' },
        ...orphans.map(a => el('div', { class: 'dept-member' },
          avatarWithStatus(a.username, a.status, 'sm'),
          el('a', { href: `#/agents/${encodeURIComponent(a.username)}`, style: 'font-weight:500', text: a.username }),
          el('span', { class: 'cell-sub', style: 'margin-left:auto', text: principalTypeLabel(a.principal_type) }),
        )),
      ),
    ));
  }
  container.append(grid);
}

export function render(container) {
  const snap = api.snapshot;
  clear(container);
  container.append(pageHeader({
    title: 'Organization',
    desc: "The agents' organization structure: live org chart, departments, roles and policies. Metadata only, never content (F18).",
  }));
  if (!snap) {
    container.append(el('div', { style: 'margin-top:24px' }, el('div', { class: 'skeleton skel-card', style: 'height:260px' })));
    return;
  }
  const orgHero = el('div', { class: 'org-hero', style: 'margin-bottom:24px' },
    el('span', { class: 'dept-icon', html: icon('organisation') }),
    el('div', { style: 'flex:1;min-width:0;display:flex;flex-direction:column;gap:6px' },
      el('span', { class: 'org-name', text: snap.organization_name }),
      el('div', { style: 'display:flex;gap:8px;flex-wrap:wrap' },
        badge(`${snap.agents.length} account${snap.agents.length > 1 ? 's' : ''}`, 'neutral'),
        badge(`${(snap.departments || []).length} department${(snap.departments || []).length > 1 ? 's' : ''}`, 'info'),
        policyBadge(api.org),
      ),
    ),
  );
  container.append(orgHero);
  const hasDepts = (snap.departments || []).length > 0;
  if (!hasDepts) {
    // Flat organization: the "small first" banner and the empty state
    // (already handled by renderDepartments) suffice — no toggle.
    renderDepartments(container);
    return;
  }
  // Representation toggle: org chart (default) / cards.
  const state = { view: 'chart' };
  const toggle = el('div', { class: 'segmented', role: 'group', 'aria-label': 'Structure representation' },
    el('button', { type: 'button', class: 'seg-btn is-active', 'aria-pressed': 'true', text: 'Org chart' }),
    el('button', { type: 'button', class: 'seg-btn', 'aria-pressed': 'false', text: 'Cards' }),
  );
  const body = el('div', { style: 'margin-top:24px' });
  toggle.querySelectorAll('button').forEach((btn, i) => {
    btn.addEventListener('click', () => {
      state.view = i === 0 ? 'chart' : 'cards';
      toggle.querySelectorAll('button').forEach((b, j) => {
        b.classList.toggle('is-active', j === i);
        b.setAttribute('aria-pressed', String(j === i));
      });
      clear(body);
      if (state.view === 'chart') renderOrgChart(body);
      else renderDepartments(body);
    });
  });
  container.append(toggle, body);
  renderOrgChart(body);
  administrationPanel(container, snap);
}

export const refresh = render;

/* ==========================================================================
   Organization administration (SPEC-WEB §4): freeze YOUR organization
   (reversible — reactivation is a local procedure). Creating
   an organization now happens FROM THE LOGIN PAGE
   (SPEC-WEB D5 amended) — it is no longer offered here.
   ========================================================================== */

function disableOrgModal(snap) {
  const orgName = snap.organization_name;
  const input = el('input', { class: 'input', type: 'text', required: true,
    placeholder: orgName, autocomplete: 'off', spellcheck: 'false',
    'aria-label': `Type ${orgName} to confirm` });
  const error = el('div', { class: 'form-error', role: 'alert', hidden: true });
  const submit = el('button', { type: 'submit', class: 'btn btn-danger', text: 'Freeze the organization' });
  const confirmFn = () => {
    if (input.value.trim() !== orgName) {
      error.textContent = 'Type the exact organization name to confirm.';
      error.hidden = false;
      return;
    }
    submit.disabled = true;
    return api.disableOrg(orgName)
      .then(() => {
        document.querySelector('.blanket')?.remove();
        toast('warn', 'Organization frozen — you will be signed out.');
        api.logout();
      })
      .catch((err) => {
        error.textContent = err.message || 'Operation failed';
        error.hidden = false;
        submit.disabled = false;
      });
  };
  const form = el('form', { class: 'form-stack', novalidate: true, onsubmit: (e) => { e.preventDefault(); confirmFn(); } },
    input, error);
  openModal({
    title: `Freeze the organization ${orgName}`,
    body: el('div', null,
      el('p', { class: 'modal-sub' },
        'Reversible freeze: no authentication nor send works anymore, all data stays intact. ' +
        'Reactivation is a local procedure (synapse-init-org --enable). Type the organization name to confirm.'),
      form),
    actions: [submit],
  });
  form.onsubmit = (e) => { e.preventDefault(); confirmFn(); };
}

function administrationPanel(container, snap) {
  const panel = el('section', { class: 'card manage-panel', 'aria-label': 'Organization administration' },
    el('div', { class: 'manage-head' },
      el('div', null,
        el('h2', { class: 'card-title' }, 'Organization administration'),
        el('p', { class: 'cell-sub' },
          'Freeze your organization (creation happens from the login page).'),
      ),
    ),
    el('div', { class: 'manage-list' },
      el('div', { class: 'manage-row' },
        el('div', { style: 'flex:1;min-width:0' },
          el('div', { class: 'manage-row-name', text: `Freeze ${snap.organization_name}` }),
          el('div', { class: 'cell-sub' }, 'Cuts all access (agents and human) without deleting anything. Local reactivation only.'),
        ),
        el('button', { class: 'btn btn-danger btn-sm', onclick: () => disableOrgModal(snap) }, 'Freeze'),
      ),
    ),
  );
  container.append(panel);
}
