# PERMISSIONS AUDIT — org / group / agent / web permission matrix
Auditor · nova_mycelium · 2026-08-12 · Scope: read-only (code + SPEC + live DB/API)
Commit: 86e898f · Package: synapse_messenger 3.1.6 (venv @ ~/.local/share/synapse)

====================================================================
## 1. MATRIX — who-can-do-what (verified against source)
====================================================================

Legend: O = org (org-password auth) · H = human account · A = agent (account)
Ob = observer (is_observer=1) · W = web-local identity (web_token)

### A. Account / org administration
| Capability                                    | O  | H  | A  | Ob | W  |
| create_agent (in own org)                     | Y  | -  | -  | -  | -  |
| deactivate / reactivate / change pw / desc    | Y  | -  | -  | -  | -  |
| set_agent_visibility (can_see_org_agents)     | Y  | -  | -  | -  | -  |
| approve_agent_card                            | Y  | -  | -  | -  | -  |
| set_agent_department / create_department      | Y  | -  | -  | -  | -  |
| set_organization_policy / escalation / budget | Y  | -  | -  | -  | -  |
| create_observer_account / revoke / list       | Y  | -  | -  | -  | -  |
| get_org_audit / metrics / structure / status  | Y  | -  | -  | -  | -  |
| create_org                                    | -  | Y  | -  | -  | Y  |
| disable_org (own only)                        | -  | Y  | -  | -  | -  |
| list_org_conversations / get_org_conversation | -  | Y  | -  | -  | -  |

### B. Agent / directory
| Capability                                    | O  | H  | A  | Ob | W  |
| get_my_organization                           | -  | Y  | Y  | Y  | -  |
| get_agent_description (public directory)      | -  | Y  | Y  | Y  | -  |
| get_agent_card (public)                       | -  | Y  | Y  | Y  | -  |
| list_org_agents (requires can_see_org_agents) | -  | Y  | gated | Y | -  |
| find_agents (requires can_see_org_agents)     | -  | Y  | gated | Y | -  |
| set_agent_card (self)                         | -  | -  | Y  | -  | -  |
| get_agent_reputation                          | -  | Y  | Y  | Y  | -  |

### C. Messaging (1:1 and groups)
| Capability                                    | O  | H  | A  | Ob | W  |
| send_message                                  | -  | Y  | Y  | -  | -  |
| get_messages / get_conversation (own convos)  | -  | Y  | Y  | Y  | -  |
| read_message (marks read)                     | -  | Y  | Y  | -  | -  |
| get_notifications                             | -  | Y  | Y  | Y  | -  |
| mark_conversation_no_reply                    | -  | Y  | Y  | -  | -  |
| create_group                                  | -  | Y  | Y  | -  | -  |
| add_group_member (any member)                 | -  | Y  | Y  | -  | -  |
| remove_group_member (creator / self only)     | -  | Y  | Y  | -  | -  |
| send_group_message (member)                   | -  | Y  | Y  | -  | -  |
| get_group_* / list_my_groups                  | -  | Y  | Y  | Y  | -  |

### D. Coordination (tasks / delegation)
| Capability                                    | O  | H  | A  | Ob | W  |
| create_task / transfer_task (cross-org!)      | -  | Y  | Y  | -  | -  |
| update_task_state (creator/assignee/delegatee)| -  | Y  | Y  | -  | -  |
| get_task / list_tasks / get_my_work / events  | -  | Y  | Y  | Y  | -  |
| request_approval (3rd-party approver)         | -  | Y  | Y  | -  | -  |
| approve_task / reject_task (assigned approver)| -  | Y  | Y  | -  | -  |
| create_delegation / revoke_delegation         | -  | Y  | Y  | -  | -  |
| list_department_tasks (manager role only)     | -  | Y  | manager | - | -  |
| get_org_snapshot                              | -  | Y  | -  | Y  | -  |

NOTE: A "Y" in the O (org) column = available to ANY principal holding
the org password (single org credential, full org admin). A "Y" in H
= the human account (password delegated to the org's).

====================================================================
## 2. GRADED FINDINGS
====================================================================

### F1 — HIGH — Cross-org task/delegation bypasses external-comm policy
Evidence: `_agent_create_task` (service.py:1601) and `_agent_transfer_task`
(:1726) and `_agent_create_delegation` (:2560) call only
`_require_active_account` + budget checks. They NEVER call
`_external_comm_allowed` (which gates send_message and add_group_member).
So in a fully closed org (allow_incoming_external=false,
allow_outgoing_external=false), an agent can still create/transfer a task
assigned to a foreign org's agent, or delegate to a foreign agent — and the
task title/description crosses the org boundary. This is DOCUMENTED as a
deliberate divergence in SPEC.txt F5 ("les politiques de communication ne
s'appliquent qu'aux messages, pas aux objets de coordination"), but from a
permission-parity standpoint it is a real exfiltration path: coordination
metadata (titles, descriptions, delegation chains) flows across org
boundaries even where messaging is fully blocked.
Fix: either (a) apply `_external_comm_allowed` to cross-org task
creation/transfer/delegation for parity with messaging, or (b) if the
divergence is intentional and desired, keep it but document it in the
permission matrix and confirm with the security seat. Recommend (a) —
an org that blocks all external messaging should also block external
task/delegation by default.

### F2 — MEDIUM — F14 granular permissions largely unimplemented
Evidence: `role` column in `memberships` (manager/employee/rh) is stored,
but the ONLY role-gated enforcement in the entire service is
`list_department_tasks` requiring role == "manager" (service.py:2106-2110).
There is NO enforcement for `rh` (SPEC promises RH reads cards, audit by
dept) and NO employee/manager differentiation for get_org_audit, cards, or
structure. SPEC.txt V.10.2 (F14) and V.7bis claim F14 is implemented, but
the granular permission matrix described ("manager voit les tâches de son
équipe, RH lit les cartes, audit par département") does not exist in code.
The roles are cosmetic metadata with a single manager-only command.
Impact: false confidence in a fine-grained model that is in reality
binary (org password = everything, agent = default). 
Fix: either implement the documented F14 gates (RH card read, dept audit
scope, employee/manager task visibility) or explicitly downgrade the F14
claim in SPEC to "role stored, only manager dept-task view enforced".

### F3 — MEDIUM — Observer labeled "metadata read" but reads full message content
Evidence: CLI helpdoc says observer = "metadata read" (cli/agent.py:174);
SPEC F18 says observer is a read-only account. But `_OBSERVER_READ_COMMANDS`
(service.py:204) includes `get_messages`, `get_conversation`,
`get_group_messages` — all of which return full message CONTENT (not just
metadata) for any conversation/group the observer participates in. An
observer can therefore read the raw body of its own 1:1 and group threads.
The "metadata read" wording understates the actual read scope. (Writes are
correctly refused at dispatch — good.)
Fix: relabel observer to "read-only (incl. own conversation content)" in
help + SPEC, or restrict observer content commands to metadata/snapshot
only, per the intent of F18.

### F4 — MEDIUM — Any group member can add members; only creator can remove
Evidence: `_agent_add_group_member` (:2305) requires only that the caller is
already a member (`_group_require_member`) + external policy. Any member can
invite an arbitrary active account into the group. `_agent_remove_group_member`
(:2328) is correctly restricted to creator-or-self. Asymmetric: a single
member can grow a group (and thereby expose all group messages to a new,
policy-permitted member) but cannot prune it. 
Fix: gate add_group_member to the group creator (or creator+adder consent),
mirroring the remove rule, unless "any member can invite" is intended.

### F5 — MEDIUM — Single org credential = full org admin (blast radius)
Evidence: `_ORG_HANDLERS` are all authed by ONE org password
(`_authenticate_organization`). Every admin command (create/deactivate
agents, set policies, budgets, depts, observers, read full audit) is granted
by that single secret. The human account's password is DELEGATED to the org
password (SPEC-WEB §5.2) and the web_token file (0600, run dir) substitutes
for it universally — I verified `policy show` and all org commands run with
just the web_token as the org password. So one 0600 file = full admin +
full human content read of EVERY org on this host.
Fix: acceptable for a single-operator local host, but document explicitly
that web_token is an org-equivalent admin credential, and consider
per-org tokens / a separate human password if the host becomes multi-user
or multi-org production.

### F6 — LOW — Test residue in live state
Evidence (live DB): nova_mycelium contains `agent_a` and `agent_b`
(disabled, unassigned, listed under unassigned_agents in org structure);
an `acme` org with `acme_humain` exists alongside bench_org and
nova_mycelium (test residue). observer accounts: none (is_observer=1 is
empty) — the F18 observer path is unused in production (web uses the human
account via web_token). 
Fix: clean up agent_a/agent_b and the acme org (or archive it) to keep the
audit/matrix surface true.

====================================================================
## 3. STRENGTHS CONFIRMED (not gaps)
====================================================================
- can_see_org_agents is enforced on BOTH list_org_agents and find_agents
  (service.py:1069, 1176) — directory/username visibility gate works; in
  live nova_mycelium only `orchestrator` carries it (correct minimal).
- Non-disclosure discipline: GROUP_NOT_FOUND / MESSAGE_NOT_FOUND /
  CONVERSATION_NOT_FOUND / RECIPIENT_NOT_FOUND vs POLICY_DENIED are used
  consistently so a closed org cannot enumerate external recipients.
- Web sessions: HttpOnly + SameSite=Strict cookie, TTL 15 min inactivity,
  max 3 sessions/org, 5-failure lockout / 15 min, token never logged.
  Solid.
- Approvals require a third party (self-approval refused); transfer
  blocked on terminal states; budget guardrails enforced.
- Human account cannot be deactivated/password-changed individually;
  org disable is the only freeze path (reversible locally).

====================================================================
## 4. RECOMMENDED FIX PRIORITY
====================================================================
1. F1 (high): gate cross-org task/delegation by external-comm policy —
   implementer, then security review.
2. F2 (medium): reconcile F14 claim with reality — implement RH/dept gates
   or downgrade SPEC. Refactorer/implementer.
3. F3 (medium): relabel or restrict observer read scope.
4. F4 (medium): add_group_member creator gate.
5. F5 (medium): document web_token admin-equivalence + consider per-org
   tokens for production.
6. F6 (low): remove test residue.

-- auditor
