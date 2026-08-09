/* ==========================================================================
   Synapse — Login screen (SPEC-WEB D5 amended).
   Two modes, accessible without a session:
     • Sign in: selection of an active organization (list served by
       the local web, trust token of the run dir) — no credential
       entry.
     • Create an organization: new organization + human account
       auto-created (web equivalent of synapse-init-org), then automatic
       login to the new organization.
   The interface stays exclusively for humans (the web is local
   and trust is carried by the service process).
   ========================================================================== */

import { el, clear } from '../ui.js';
import { icon, brandMark } from '../icons.js';
import { api } from '../api.js';

const ORG_NAME_RE = /^[a-z0-9_-]{3,64}$/;

export function render(root) {
  clear(root);

  const errorBox = el('div', { class: 'login-error', role: 'alert', hidden: true });
  const seg = el('div', { class: 'segmented login-mode', role: 'group',
    'aria-label': 'Connection mode' });
  const btnLogin = el('button', { type: 'button', class: 'seg-btn is-active',
    text: 'Sign in' });
  const btnCreate = el('button', { type: 'button', class: 'seg-btn',
    text: 'Create an organization' });
  seg.append(btnLogin, btnCreate);

  const form = el('form', { class: 'login-form', novalidate: true });

  // Fixed elements of the login card (the flex column order of the
  // form provides spacing): the active mode's elements are
  // inserted between the switch and the error area.
  form.append(
    el('div', { class: 'login-brand', html: brandMark(40) }),
    el('h1', { class: 'login-title' }, 'Synapse'),
    el('p', { class: 'login-sub' },
      'Supervision interface for AI agent organizations.',
      el('br'),
      'Access restricted to human accounts.'),
    seg,
  );
  let modeNodes = [];
  const insertMode = (nodes) => {
    for (const n of modeNodes) n.remove();
    modeNodes = nodes;
    for (const n of modeNodes) form.insertBefore(n, errorBox);
  };
  form.append(errorBox,
    el('p', { class: 'login-hint', html: icon('lock', 12) },
      ' Automatic local login — no data stored on this device.'));

  root.append(el('div', { class: 'login-screen' }, form));

  const switchMode = (create) => {
    btnLogin.classList.toggle('is-active', !create);
    btnCreate.classList.toggle('is-active', create);
    errorBox.hidden = true;
    form.onsubmit = null;
    insertMode(create ? renderCreateFields() : renderLoginFields(switchMode));
  };
  btnLogin.onclick = () => switchMode(false);
  btnCreate.onclick = () => switchMode(true);
  switchMode(false);
}

/* --- "Sign in" mode: select an existing organization --- */

function renderLoginFields(switchMode) {
  const select = el('select', {
    name: 'organization_name', class: 'login-input login-select', required: true,
    'aria-label': 'Organization', id: 'login-org',
  });
  select.append(el('option', { value: '', selected: true, hidden: true },
    'Loading organizations…'));
  const btn = el('button', {
    type: 'submit', class: 'btn btn-primary login-submit', disabled: true,
  }, 'Sign in');

  const form = document.querySelector('.login-form');
  form.onsubmit = (e) => {
    e.preventDefault();
    submitLogin(btn, select);
  };

  loadOrganizations(select, btn, switchMode);
  return [
    el('label', { class: 'login-label', for: 'login-org' }, 'Organization'),
    select,
    btn,
  ];
}

/** Fills the dropdown with the active organizations. */
async function loadOrganizations(select, btn, onNoOrg) {
  try {
    const orgs = await api.listOrgs();
    if (!orgs.length) {
      // No organization yet: jump straight to the creation form —
      // there is nothing to sign in to, a second click on
      // "Create an organization" would be pure friction.
      select.replaceChildren(el('option', { value: '', selected: true },
        'No organization available'));
      setError('No active organization on this service — create one.');
      if (onNoOrg) onNoOrg(true);
      return;
    }
    select.replaceChildren(
      el('option', { value: '', selected: true, disabled: true, hidden: true },
        'Choose an organization…'),
      ...orgs.map(o => el('option', { value: o }, o)),
    );
    select.disabled = false;
    btn.disabled = false;
    select.focus();
  } catch {
    select.replaceChildren(el('option', { value: '', selected: true },
      'Service unavailable'));
    setError('Cannot load the organizations (local service unreachable).');
  }
}

async function submitLogin(btn, select) {
  const org = select.value;
  if (!org) {
    setError('Choose an organization.');
    return;
  }
  btn.disabled = true;
  btn.textContent = 'Connecting…';
  hideError();
  try {
    await api.login(org);
    await api.bootstrap();  // loads the snapshot (the shell is already mounted)
    location.hash = '#/dashboard';
  } catch (err) {
    setError(err.message || 'Organization unavailable.');
    btn.disabled = false;
    btn.textContent = 'Sign in';
    select.focus();
  }
}

/* --- "Create an organization" mode --- */

function renderCreateFields() {
  const orgName = el('input', { class: 'login-input', type: 'text', required: true,
    placeholder: 'e.g. org_nouvelle', spellcheck: 'false', autocomplete: 'off',
    'aria-label': 'Name of the new organization (3-64 [a-z0-9_-])', id: 'create-org-name' });
  const password = el('input', { class: 'login-input', type: 'password', required: true,
    minlength: 12, placeholder: 'Password (>= 12 characters)',
    autocomplete: 'new-password',
    'aria-label': 'Password of the new organization', id: 'create-org-password' });
  const confirm = el('input', { class: 'login-input', type: 'password', required: true,
    minlength: 12, placeholder: 'Confirmation', autocomplete: 'new-password',
    'aria-label': 'Password confirmation', id: 'create-org-confirm' });
  const btn = el('button', { type: 'submit', class: 'btn btn-primary login-submit' },
    'Create and sign in');
  const note = el('p', { class: 'login-sub create-org-note' },
    'The human account of the new organization is created automatically.');

  const form = document.querySelector('.login-form');
  form.onsubmit = (e) => {
    e.preventDefault();
    submitCreate(btn, orgName, password, confirm);
  };

  orgName.focus();
  return [
    el('label', { class: 'login-label', for: 'create-org-name' }, "Organization name"),
    orgName,
    el('label', { class: 'login-label', for: 'create-org-password' }, 'Password'),
    password,
    el('label', { class: 'login-label', for: 'create-org-confirm' }, 'Confirmation'),
    confirm,
    btn,
    note,
  ];
}

async function submitCreate(btn, orgName, password, confirm) {
  const name = orgName.value.trim().toLowerCase();
  if (!ORG_NAME_RE.test(name)) {
    setError('Invalid name: 3-64 characters [a-z0-9_-] (lowercase).');
    orgName.focus();
    return;
  }
  if (password.value.length < 12) {
    setError('Password too short: 12 characters minimum.');
    password.focus();
    return;
  }
  if (password.value !== confirm.value) {
    setError('The two password entries differ.');
    confirm.focus();
    return;
  }
  btn.disabled = true;
  btn.textContent = 'Creating…';
  hideError();
  try {
    await api.createOrg(name, password.value);
    // Automatic login: same behavior as selecting an
    // existing organization (the web authenticates by local token on behalf
    // of the human account of the new organization).
    await api.login(name);
    await api.bootstrap();
    location.hash = '#/dashboard';
  } catch (err) {
    setError(err.message || 'Creation failed.');
    btn.disabled = false;
    btn.textContent = 'Create and sign in';
    orgName.focus();
  }
}

/* --- Errors --- */

function setError(message) {
  const box = document.querySelector('.login-error');
  if (!box) return;
  box.textContent = message;
  box.hidden = false;
}

function hideError() {
  const box = document.querySelector('.login-error');
  if (box) box.hidden = true;
}
