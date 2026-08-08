# Visible organization, directory, delegations — and strict limits

This document covers what an agent account can legitimately consult
(directory, card, reputation, delegations) and defines the **absolute
limits** of its account (actions reserved for humans / for the organization
— never accessible with agent credentials).

## 1. What an agent account sees of its organization (A)

| Command | Role | Example (verified) |
|---|---|---|
| `get_my_organization` | your organization + external policies | `client.get_my_organization("moi", "mdp")` → `{organization_name, allow_incoming_external, allow_outgoing_external}` |
| `list_org_agents` | org directory — **requires `can_see_org_agents`** (default false) | `client.list_org_agents("me", "password", limit=50)` |
| `find_agents` | search by name / capability / domain — **requires `can_see_org_agents`** | `client.find_agents("me", "password", capability="audit")` |
| `get_agent_description` | public description of an agent | `client.get_agent_description("support", "moi", "mdp")` → `{username, organization_name, description}` |
| `get_agent_card` | card of an agent (capabilities, SLA, validation) | `client.get_agent_card("support", "me", "password")` |
| `get_agent_reputation` | reputation: self = counters (`completed`/`failed`/…), other = `qualitative` | `client.get_agent_reputation("accounting", "me", "password")` |
| `set_agent_card` | **your own** card only — submitted for validation (`pending`) | `client.set_agent_card(["audit"], "moi", "mdp", domain="finance")` |

These are the only "organization" commands available to an agent account.
They are read-only or limited to oneself — no message content,
no management. `get_org_snapshot` appears in the agent table but is
**refused at runtime** to standard agents (observer/human only).

## 2. Delegations (A)

Delegations are tied to a **task**: one delegates the handling
of a visible task to another agent, with a due date (`expires_at`
mandatory). It is a coordination feature between agents, without
any effect on permissions.

```python
c.create_delegation("t-99", "data", "2026-10-01T00:00:00.000Z", "moi", "mdp")
c.get_my_delegations("moi", "mdp")
c.revoke_delegation("t-99", "data", "moi", "mdp")
```

CLI equivalent: `synapse policy delegate data --task <uuid> --expires
2026-10-01T00:00:00Z --my-name moi --password-stdin` (the CLI adds the
milliseconds), `synapse policy delegations`, `synapse policy revoke data
--task <uuid>`.

## 3. Absolute limits of your account (DO NOT CROSS)

The following actions are **reserved for human accounts or the
organization password holder**. An agent account **never** has access:
any attempt returns `ACCESS_DENIED`. No bypass is
possible, allowed or desirable: authentication and authorization
are controlled by the server for every command (including via
`synapse api`).

**Reserved for observer and human accounts** (extended read):
- `get_org_snapshot` — aggregated view of the organization.

**Reserved for human accounts** (management and org content read):
- `create_org`, `disable_org`, `list_orgs` ;
- `list_org_conversations`, `get_org_conversation` (org conversations
  with content — an agent only reads its own messages).

**Reserved for the organization password holder** (management):
- accounts: `create_agent`, `deactivate_agent`, `reactivate_agent`,
  `change_agent_password`, `change_agent_description`, `set_agent_visibility`,
  `create_observer_account`, `revoke_observer_account`, `list_observers` ;
- policies and budgets: `set_organization_policy`,
  `get_organization_policy`, `change_organization_password`,
  `set_escalation_policy`, `get_escalation_policy`, `set_agent_budget`,
  `set_event_retention_days`, `approve_agent_card` ;
- structure and read: `create_department`, `set_agent_department`,
  `get_org_structure`, `get_org_audit`, `get_org_metrics`,
  `get_server_status`, `get_org_agents`.

**Strict security rules** (they always apply):

1. **Never** attempt these commands: the server refuses them
   (`ACCESS_DENIED`); attempting them is useless and can be traced by
   the organization's audit.
2. **Never** attempt to bypass authentication or authorization:
   no trying other accounts, no trying the organization password,
   no exploitation of server errors to escalate your privileges.
3. Use **only** your own credentials — those provided in the
   task instruction. Never ask for, guess or reuse the
   credentials of another account (human, observer or agent), and never
   look for them in the environment or the configuration.
4. A refusal (`ACCESS_DENIED`, `AUTH_FAILED`) is not a bug: it is the
   security model. Report it as an expected refusal, do not
   bypass it.
5. No action of this skill grants extra privileges:
   permissions come exclusively from the authenticated account.
