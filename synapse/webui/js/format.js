/* ==========================================================================
   Synapse — Formatting & labels
   ========================================================================== */

export function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

const MINUTE = 60_000, HOUR = 3_600_000, DAY = 86_400_000;

export function timeAgo(iso) {
  if (!iso) return '—';
  const then = new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime();
  if (Number.isNaN(then)) return esc(iso);
  const diff = Date.now() - then;
  if (diff < 45_000) return "just now";
  if (diff < HOUR) return `il y a ${Math.round(diff / MINUTE)} min`;
  if (diff < DAY) return `il y a ${Math.round(diff / HOUR)} h`;
  if (diff < 7 * DAY) {
    const d = Math.round(diff / DAY);
    return d === 1 ? 'hier' : `il y a ${d} j`;
  }
  return new Date(then).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

export function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  return d.toLocaleString('en-GB', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

export function shortId(uuid) {
  if (!uuid) return '—';
  return uuid.length > 8 ? uuid.slice(0, 8) : uuid;
}

export const STATE_LABELS = {
  submitted: 'Soumise',
  in_progress: 'In progress',
  pending_approval: 'Approbation',
  completed: 'Completed',
  failed: 'Failed',
  canceled: 'Canceled',
};
export const STATE_TONES = {
  submitted: 'info',
  in_progress: 'accent',
  pending_approval: 'warn',
  completed: 'ok',
  failed: 'danger',
  canceled: 'neutral',
};
export const ACTIVE_STATES = new Set(['submitted', 'in_progress', 'pending_approval']);

export const PRIORITY_LABELS = { low: 'Basse', normal: 'Normale', high: 'Haute' };

export const COMMAND_LABELS = {
  create_agent: 'agent creation',
  deactivate_agent: 'agent deactivation',
  reactivate_agent: 'agent reactivation',
  change_agent_password: 'rotation de mot de passe',
  set_agent_visibility: 'directory visibility',
  send_message: 'message sent',
  create_task: 'task creation',
  update_task_state: 'task state change',
  transfer_task: 'task transfer',
  request_approval: 'approval request',
  approve_task: 'approval',
  reject_task: 'task rejection',
  create_department: 'department creation',
  set_agent_department: 'department assignment',
  set_agent_card: 'card update',
  approve_agent_card: 'card validation',
  create_group: 'group creation',
  send_group_message: 'group message',
  set_escalation_policy: 'escalation policy',
  set_agent_budget: 'agent budget',
  create_observer_account: 'observer creation',
  revoke_observer_account: 'observer revocation',
  change_organization_password: 'organization password rotation',
  set_organization_policy: 'organization policy',
};
export function commandLabel(cmd) {
  return COMMAND_LABELS[cmd] || cmd;
}

export function stateLabel(s) { return STATE_LABELS[s] || s; }

export function principalTypeLabel(t) {
  if (t === 'human') return 'Humain';
  if (t === 'observer') return 'Observateur';
  return 'Agent';
}

export function isDueSoon(dueIso) {
  if (!dueIso) return null; // no due date
  const due = new Date(dueIso.endsWith('Z') ? dueIso : dueIso + 'Z').getTime();
  const now = Date.now();
  if (due < now) return 'overdue';
  if (due - now < DAY) return 'today';
  return null;
}

/* Deterministic avatar gradient (stable for a given username).
   "Registre" palette: warm OKLCH (anchor 40-75°), saturated but sober,
   aligned with the system hues (vermilion, amber, olive, steel blue,
   prune). The initials ink is the paper ivory (AA contrast on
   the mid gradients). */
const AVATAR_PALETTE = [
  ['oklch(0.62 0.16 40)', 'oklch(0.78 0.11 75)'],
  ['oklch(0.56 0.13 25)', 'oklch(0.72 0.12 55)'],
  ['oklch(0.60 0.14 90)', 'oklch(0.78 0.10 115)'],
  ['oklch(0.52 0.10 250)', 'oklch(0.70 0.10 200)'],
  ['oklch(0.55 0.11 300)', 'oklch(0.72 0.09 330)'],
  ['oklch(0.58 0.12 170)', 'oklch(0.76 0.09 150)'],
  ['oklch(0.60 0.13 60)', 'oklch(0.75 0.10 95)'],
  ['oklch(0.54 0.12 15)', 'oklch(0.70 0.11 45)'],
];
function hashName(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return h;
}
export function avatarStyle(username) {
  const [c1, c2] = AVATAR_PALETTE[hashName(username) % AVATAR_PALETTE.length];
  return `background:linear-gradient(135deg,${c1},${c2});color:oklch(0.98 0.01 60)`;
}
export function avatarInitial(username) {
  return (username || '?').charAt(0).toUpperCase();
}
