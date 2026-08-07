/* ==========================================================================
   Synapse — API layer: web session, snapshot, polling, connection state.
   Login (organization + password) creates a server-side session
   (cookie HttpOnly SameSite=Strict) ; plus aucun jeton statique n'est
   requested by the browser (SPEC-WEB §5-§6). All calls go through the
   local HTTP server which goes through the Synapse socket with the identity of the
   compte humain de la session.
   ========================================================================== */

import { ACTIVE_STATES } from './format.js';

const SNAPSHOT_URL = '/api/snapshot';
const POLL_INTERVAL = 5000;

class Api {
  constructor() {
    this.snapshot = null;      // last decoded snapshot
    this.org = null;           // informations d'organisation
    this.session = null;       // {organization_name, human_username, expires_at}
    this.status = 'connecting'; // connecting | live | stale | off | signed-out
    this.error = null;         // message d'erreur de connexion
    this.lastUpdate = null;    // Date of the last successful update
    this.latency = null;       // ms du dernier aller-retour
    this._etag = null;
    this._timer = null;
    this._listeners = new Set();
    this._snapshotJson = '';
    this._fails = 0;
    this._sessionListeners = new Set();
  }

  /* ---- Session -------------------------------------------------------- */
  onSessionChange(fn) { this._sessionListeners.add(fn); return () => this._sessionListeners.delete(fn); }
  _emitSession() { for (const fn of this._sessionListeners) { try { fn(this.session); } catch (e) { console.error(e); } } }

  get authenticated() {
    return Boolean(this.session);
  }

  /** Organization selection login (SPEC-WEB D5 amended): no more
   *  typed password — the web server authenticates itself to the
   *  service (jeton de confiance local) et pose le cookie de session. */
  async login(organization_name) {
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ organization_name }),
      cache: 'no-cache',
    });
    let body = {};
    try { body = await res.json(); } catch { /* corps vide */ }
    if (res.status === 429) {
      throw new Error('Too many attempts. Try again in a few minutes.');
    }
    if (!res.ok) {
      throw new Error(body.error || 'Organisation indisponible');
    }
    this.session = body;
    this.status = 'live';
    this.error = null;
    this._emitSession();
    return body;
  }

  /** List of active organizations for the login screen. */
  async listOrgs() {
    const res = await fetch('/api/orgs', { cache: 'no-cache' });
    if (!res.ok) return [];
    const body = await res.json().catch(() => ({}));
    return (body.organizations || []).map(o => o.organization_name);
  }

  async logout() {
    try { await fetch('/api/logout', { method: 'POST', cache: 'no-cache' }); } catch { /* hors-ligne */ }
    this._resetSession();
  }

  _resetSession() {
    this.session = null;
    this.snapshot = null;
    this.org = null;
    this.status = 'signed-out';
    this._snapshotJson = '';
    this._etag = null;
    this._emitSession();
  }

  /** Checks the existing session at startup (cookie already set). */
  async checkSession() {
    try {
      const res = await fetch('/api/session', { cache: 'no-cache' });
      if (res.status === 401) {
        this._resetSession();
        return false;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      this.session = await res.json();
      this.status = 'live';
      this._emitSession();
      return true;
    } catch {
      this._resetSession();
      this.status = 'off';
      this.error = 'Serveur injoignable';
      this._emitSession();
      return false;
    }
  }

  /* ---- Souscription ---------------------------------------------------- */
  onUpdate(fn) { this._listeners.add(fn); return () => this._listeners.delete(fn); }
  _emit() { for (const fn of this._listeners) { try { fn(this.snapshot); } catch (e) { console.error(e); } } }

  /* ---- JSON request (If-None-Match, cookie session) ---------------- */
  async fetchJSON(path, { etag = false, post = null } = {}) {
    const headers = {};
    if (etag && this._etag) headers['If-None-Match'] = this._etag;
    const opts = { headers, cache: 'no-cache' };
    if (post) {
      opts.method = 'POST';
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(post);
    }
    const t0 = performance.now();
    const res = await fetch(path, opts);
    this.latency = Math.round(performance.now() - t0);
    if (res.status === 401) {
      // Session expired or revoked: back to the login screen.
      this._resetSession();
      throw new Error('session expired');
    }
    if (res.status === 304) return { notModified: true };
    if (!res.ok) {
      let detail = '';
      try { detail = (await res.json()).error || ''; } catch { /* corps non JSON */ }
      throw new Error(detail || `HTTP ${res.status}`);
    }
    const et = res.headers.get('ETag');
    if (et) this._etag = et;
    return { data: await res.json() };
  }

  /* ---- Snapshot --------------------------------------------------------- */
  async poll() {
    try {
      const out = await this.fetchJSON(SNAPSHOT_URL, { etag: true });
      if (!out.notModified) {
        const json = JSON.stringify(out.data);
        const changed = json !== this._snapshotJson;
        this._snapshotJson = json;
        this.snapshot = out.data;
        if (changed) {
          this.lastUpdate = new Date();
          this._emit();
        }
      }
      const prevStatus = this.status;
      this._fails = 0;
      this.status = 'live';
      this.error = null;
      if (prevStatus !== 'live') this._emit();
    } catch (e) {
      this._fails += 1;
      this.error = e.message || 'connection lost';
      // stale: the server still responds but the state is old; off: failure.
      this.status = this._fails > 2 ? 'off' : (this.snapshot ? 'stale' : 'off');
      if (this.status === 'off') this._emit();
    }
  }

  async bootstrap() {
    const authed = await this.checkSession();
    if (!authed) return;
    try {
      const out = await this.fetchJSON(SNAPSHOT_URL, { etag: true });
      if (!out.notModified) {
        this._snapshotJson = JSON.stringify(out.data);
        this.snapshot = out.data;
        this.lastUpdate = new Date();
      }
      this.status = 'live';
      this.error = null;
    } catch (e) {
      this.status = this.session ? 'off' : 'signed-out';
      this.error = e.message || 'cannot reach the server';
    }
    try {
      const { data } = await this.fetchJSON('/api/org');
      this.org = data;
    } catch { /* l'org n'est pas bloquant */ }
    this._emit();
  }

  start() {
    if (this._timer) return;
    this._timer = setInterval(() => this.poll(), POLL_INTERVAL);
  }
  stop() {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
  }

  /* ---- Conversations (SPEC-WEB §2) -------------------------------------- */
  async conversations() {
    const { data } = await this.fetchJSON('/api/conversations');
    return data.conversations || [];
  }

  async conversation(conversation_id) {
    const { data } = await this.fetchJSON(
      `/api/conversation?conversation_id=${encodeURIComponent(conversation_id)}`);
    return data;
  }

  async sendMessage(recipient_username, message) {
    const { data } = await this.fetchJSON('/api/send', {
      post: { recipient_username, message },
    });
    return data;
  }

  /* ---- Gestion des agents (SPEC-WEB §4) --------------------------------- */
  async createAgent(username, password, description) {
    const { data } = await this.fetchJSON('/api/agents', {
      post: { username, password, description },
    });
    return data;
  }

  async deactivateAgent(username) {
    return (await this.fetchJSON(
      `/api/agents/${encodeURIComponent(username)}/deactivate`, { post: {} })).data;
  }

  async reactivateAgent(username) {
    return (await this.fetchJSON(
      `/api/agents/${encodeURIComponent(username)}/reactivate`, { post: {} })).data;
  }

  async changeAgentDescription(username, description) {
    return (await this.fetchJSON(
      `/api/agents/${encodeURIComponent(username)}/description`,
      { post: { description } })).data;
  }

  /* ---- Gestion des organisations (SPEC-WEB §4) --------------------------- */
  async createOrg(organization_name, organization_password) {
    return (await this.fetchJSON('/api/orgs', {
      post: { organization_name, organization_password },
    })).data;
  }

  async disableOrg(organization_name) {
    return (await this.fetchJSON('/api/orgs/disable', {
      post: { organization_name },
    })).data;
  }

  /* ---- Derived data -------------------------------------------------- */
  agentByUsername(name) {
    return this.snapshot?.agents.find(a => a.username === name) || null;
  }
  departmentOf(username) {
    for (const d of this.snapshot?.departments || []) {
      const m = d.members.find(x => x.username === username);
      if (m) return { department: d.department_name, role: m.role };
    }
    return null;
  }
  agentsByStatus() {
    const out = { active: 0, disabled: 0, observer: 0, human: 0 };
    for (const a of this.snapshot?.agents || []) {
      if (a.is_observer) out.observer += 1;
      if (a.status === 'active') out.active += 1; else out.disabled += 1;
      if (a.principal_type === 'human' && !a.is_observer) out.human += 1;
    }
    return out;
  }
  taskCounts() {
    const s = this.snapshot?.tasks_by_state || {};
    const active = (s.submitted || 0) + (s.in_progress || 0) + (s.pending_approval || 0);
    const done = (s.completed || 0) + (s.failed || 0) + (s.canceled || 0);
    return { ...s, active, done, total: active + done };
  }
  unreadTotal() {
    return (this.snapshot?.conversations || []).reduce((n, c) => n + (c.unread_count || 0), 0);
  }
  overdueTasks(limit = 6) {
    const now = Date.now();
    return (this.snapshot?.tasks || []).filter(t =>
      ACTIVE_STATES.has(t.state) && t.due_at &&
      new Date(t.due_at.endsWith('Z') ? t.due_at : t.due_at + 'Z').getTime() < now
    ).slice(0, limit);
  }
  conversationsNeedingReply(limit = 6) {
    return (this.snapshot?.conversations || [])
      .filter(c => (c.unread_count || 0) > 0)
      .slice(0, limit);
  }
  agentConversations(username, limit = 8) {
    return (this.snapshot?.conversations || [])
      .filter(c => c.a === username || c.b === username)
      .slice(0, limit);
  }
  agentTasks(username, limit = 12) {
    return (this.snapshot?.tasks || [])
      .filter(t => t.creator_username === username || t.assignee_username === username)
      .slice(0, limit);
  }
}

export const api = new Api();
