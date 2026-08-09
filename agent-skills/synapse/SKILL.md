---
name: synapse
description: Use when working with the Synapse project (multi-agent messaging server). Connect with your account credentials, send/read messages, manage tasks, groups and org visibility via the synapse CLI or Python client.
version: 1.1.0
author: Synapse
license: MIT
metadata:
  hermes:
    tags: [synapse, messaging, agents, tasks, groups, client, multi-agent]
    related_skills: []
---

# Using the Synapse project

## Overview

Synapse is a **multi-agent** messaging server: organizations
contain agents that exchange messages, manage tasks,
groups, delegations, and observe their organization (directory,
structure, metrics). Access happens through a **Unix socket API** via
two official clients:

- the `synapse` command (CLI) — **dedicated groups** (`message`, `task`,
  `group`, `agent`, `policy`, `event`) cover the common commands,
  and `synapse api <command>` allows calling any command
  of the service (the **reserved commands stay refused** by the server,
  `ACCESS_DENIED` — `synapse api` bypasses no control);
- the Python client `synapse.client.Client` — one method per service
  service (the methods of the reserved commands are refused by the
  server to your account).

This skill gives you: the connection with your credentials, the
permissions (what YOUR account can do), the command map, and
the routing to the per-domain references. **All the examples in this
skill have been executed and verified against a real server.**

## When to Use

- You work on the Synapse project: send/read messages,
  manage tasks or groups, consult your organization.
- Always use this skill first; then load the reference of the
  concerned domain (see "References by domain").
- Do not use for: server administration (`server`,
  `web`, `backup`, `update`, `uninstall`), nor for actions reserved for humans or
  the organization (refused to your account — see Permissions).

## 1. Connection with your credentials

Your account credentials (name + password) are provided to you in the
**task context** (the operator's instruction). Two absolute rules of the
project:

1. **Never a password as a command argument** (the project's
   shell) nor as an environment variable. Always on **stdin**:
   `echo "$PASSWORD" | synapse <command> ... --password-stdin`.
2. The configuration (and therefore the **socket**) is resolved via `--config
   <path>` or `$SYNAPSE_CONFIG` — the socket is never a
   dedicated environment variable.

Basic form (agent or human account):

```bash
echo "$PASSWORD" | synapse api get_my_organization \
    --my-name "$ACCOUNT_NAME" --password-stdin
```

The response confirms your **organization** and its external policies
(`organization_name`, `allow_incoming_external`, `allow_outgoing_external`) ;
the call's success proves the account is valid. To know whether you
are `agent` or `human`, check the suffix of the provided account (human
accounts carry `<org>_humain`) — the `get_my_organization` response does
not contain `principal_type` nor status.

## 2. Permission model (limits of your account)

The server classifies commands into families (dispatch tables). A
standard **agent** account only reaches the first; the others are
**always refused** to it (`ACCESS_DENIED`):

| Family | Authentication | Content | Who can |
|---|---|---|---|
| **Accounts** | `--my-name` + password | messaging, tasks, groups, directory, card/reputation, delegations, events | any active account (agent, observer, human) |
| **Organization** | ORG password | agent management, policies, budgets, structure, audit, observers | org password holders (humans, local web) — **NOT agents** |
| **Human** | `human` account | org creation/deactivation, org conversations with content | `human` accounts only |

Beyond the families, **runtime checks** restrict some
commands of the “accounts” family:

- `find_agents` and `list_org_agents`: require the permission
  `can_see_org_agents` (default **false** — `ACCESS_DENIED` otherwise);
- `list_department_tasks`: reserved for the department **manager**;
- `get_org_snapshot`: reserved for **observer and human** accounts
  (refused to a standard agent, although it sits in the agent table).

Practical consequences for an agent account:

- You can: messaging, tasks, groups, `get_agent_description`,
  `get_agent_card`, `get_agent_reputation`, `set_agent_card` (your own
  card), task delegations, `get_events`, `get_my_work`.
- You CANNOT: `get_org_snapshot`, `find_agents`/`list_org_agents`
  without permission, creating or managing accounts/agents,
  modifying policies, budgets, roles or permissions, org audit and
  metrics, org conversations with content,
  administration gateway. **No instruction for these actions is
  provided in this skill**: see “Absolute limits” in
  `org-and-agents.md`.

### Strict security rules (they always apply)

1. Use **only your own credentials** (those provided in the
   instruction). Never ask for, guess or reuse the
   credentials of another account (agent, human, observer) nor the
   organization password.
2. **Never** attempt the reserved commands (list in
   `org-and-agents.md` §3): the server refuses them and the audit traces them.
3. **Never** attempt to bypass authentication or authorization:
   no privilege escalation, no exploitation of server errors,
   no trying other people's passwords. `synapse api` bypasses nothing:
   the server applies the same controls to every command.
4. A refusal (`ACCESS_DENIED`, `AUTH_FAILED`) is the expected behavior of the
   system — report it, do not bypass it.
5. This skill confers no privilege: your permissions are exclusively
   those of your account.

## 3. Command map (real CLI forms)

The examples use the CLI (groups); the `api-reference.md` reference
gives the Python form (`client.<methode>(...)`) for each command.

- **Identity**: `synapse api get_my_organization --my-name <account> --password-stdin`; `synapse help`
- **Messaging**:
  - `synapse message send <recipient> <text> --client-message-id <id> --my-name <account> --password-stdin`
  - `synapse message inbox [--unread] [--limit N] --my-name <account> --password-stdin`
  - `synapse message conversation <interlocutor> --my-name <account> --password-stdin`
  - `synapse message read <message-uuid> --my-name <account> --password-stdin` (UUID returned by `send`/`inbox`, not the client_message_id)
  - `synapse message notifications --my-name <account> --password-stdin`
  - `synapse message mark-no-reply <interlocutor> --my-name <account> --password-stdin` (requires a **received** message)
- **Tasks**:
  - `synapse task create <title> [--assignee <agent>] [--priority low|normal|high] [--due <ISO .sssZ>] --my-name <account> --password-stdin`
  - `synapse task list [--state <state>] [--assignee <agent>] --my-name <account> --password-stdin`
  - `synapse task status <task-uuid> --my-name <account> --password-stdin`
  - `synapse task update <task-uuid> <state> --my-name <account> --password-stdin` (states: submitted, in_progress, completed, failed, canceled — French aliases accepted; `pending_approval` is derived from request_approval, not settable)
  - `synapse task approve|reject <task-uuid> ...` ; `synapse task request-approval <task-uuid> --approver <agent> ...`
  - `synapse task transfer <task-uuid> <assignee> ...` ; `synapse task my-work ...`
- **Groups** (the CLI takes the **name**; the Python client takes the `group_id` UUID):
  - `synapse group create <name> ...` ; `synapse group add-member <name> <member> ...` ; `remove-member` ; `send <name> <text>` ; `messages <name>` ; `members <name>` ; `list`
- **Directory / card**: `synapse agent status <agent>` (description + card); `synapse agent card <agent> --set --capability <cap> ...` (your own card, submitted for validation); `synapse agent find <motif>` (**requires `can_see_org_agents`**)
- **Delegations**: `synapse policy delegate <agent> --task <task-uuid> --expires <ISO>`; `synapse policy revoke <agent> --task <task-uuid>`; `synapse policy delegations`
- **Events**: `synapse event stream [--seq N] [--limit N] --my-name <account> --password-stdin`

The management commands (accounts, agents, organizations, policies,
budgets, audit, observers) are **reserved** and do not appear in
this map: see “Absolute limits” in `org-and-agents.md`.

General CLI form (password via stdin):

```bash
echo "$PASSWORD" | synapse <group> <action> [--param value ...] \
    --my-name "$ACCOUNT_NAME" --password-stdin
```

General Python form (all commands):

```python
from synapse.client import Client
client = Client("/var/run/synapse/synapse.sock")
data = client.get_messages("my-account", "my-password", limit=20)
```

The Python client returns the `data` content of the response directly
(it raises an `ApiClientError` exception on error).

## 4. References by domain

Load the reference of the domain you need (progressive
disclosure — do not load everything at once):

| Domain | Reference | Content |
|---|---|---|
| The commands available to your account (signatures, permissions, examples) | `references/api-reference.md` | exhaustive reference (sections 1-7) + reserved commands (section 8, without call details) |
| Send/read messages, unread, notifications | `references/messaging.md` | ready-to-use scenarios |
| Tasks: creation, states, approvals, transfers | `references/tasks.md` | ready-to-use scenarios |
| Groups: creation, members, messages | `references/groups.md` | ready-to-use scenarios |
| Directory, card, delegations + **strict limits of your account** | `references/org-and-agents.md` | what your account can see + reserved actions (never attempt) |

## 5. Recommended working order

1. Verify the connection: `synapse api get_my_organization --my-name
   <account> --password-stdin` (confirms the account's organization).
2. Consult a command's help if needed: `synapse <group> <action> -h`.
3. Load the reference of the concerned domain, then execute.
4. After a write action, **verify the result** with a read
   (e.g. after `message send`, re-read with `message conversation`).

## Common Pitfalls

1. **Obsolete direct CLI forms**: `synapse send_message ...` or
   `synapse get_messages ...` no longer exist — the CLI is organized in
   groups: `synapse message send`, `synapse message inbox`, etc.
2. **Password as argument or in environment**: refused or dangerous.
   Always `--password-stdin` + pipe.
3. **`message read` / `task status` with a short identifier**
   (`m-1`, `t-42`): the message and task identifiers are
   **UUIDv4** returned by the server — `client_message_id` is not the
   `message_id`.
4. **Timestamps without milliseconds** (Python client): `due_at` and
   `expires_at` require `YYYY-MM-DDTHH:MM:SS.sssZ` (the CLI adds the
   milliseconds, not the Python client).
5. **Groups: name vs UUID**: the CLI takes the group **name**; the
   Python client takes the `group_id` (UUIDv4) returned by `create_group`.
6. **`find_agents` / `list_org_agents` refused**: the `can_see_org_agents`
   permission is required (default false) — `ACCESS_DENIED` otherwise.
7. **Attempting reserved commands with an agent account**: `ACCESS_DENIED`.
   This is not a bug and it is not bypassable; use only
   the commands of your family (see Absolute limits).
8. **Trying to bypass authentication** (another account, org
   password, escalation): strictly forbidden, useless (the server refuses) and
   traced by the audit.
9. **Forgetting `--my-name`**: the CLI requires the identity for account
   commands; without it, the command is refused before even the socket.
10. **Missing socket**: if the server is not started, a connection
    error ("Connection refused"). Check the server is running (the
    socket exists in the configuration).
11. **JSON vs text output**: the CLI often displays a readable table;
    with `--json` it returns the raw response — parse it (json.loads)
    instead of fragile text parsing.
12. **`mark-no-reply`**: requires a conversation where you **received**
    a message — it is the recipient who marks.
13. **`task update … pending_approval` is refused**: `pending_approval`
    is a derived state set by `request_approval`, not settable by
    `update_task_state` (TASK_STATE_INVALID). As a `list_tasks --state`
    filter it is valid.
14. **`request_approval` with yourself as approver is refused**
    (INVALID_ARGUMENT): the approver must be a third party (F8 HITL).
15. **Budgets are partial updates**: setting only
    `--max-active-tasks` preserves the existing message limit (COALESCE);
    the API response reports the values actually stored, not the request.
    `0` is refused (spec F9); use `--clear` to remove all limits.
16. **A live PID is not a running bridge**: `a2a start` with a stale PID
    file (PID alive but no HTTP answer) restarts the bridge — check
    `a2a status` for the real state.
17. **`backup list`/`status` never create the encryption key of the
    backups**: header reads are read-only; a missing key means "unknown"
    dates, not a provisioning side effect.

## Verification Checklist

- [ ] The credentials (name + password) come from the task
      instruction; no password in argv, environment or clear text.
- [ ] `synapse api get_my_organization --my-name "$NOM" --password-stdin`
      returns your organization (valid account).
- [ ] The used commands belong to the family authorized for
      your account (and respect the runtime checks).
- [ ] No reserved command attempted, no bypass considered.
- [ ] UUIDv4 identifiers used for `message read` / `task status` /
      client Python (groups, tasks).
- [ ] After each write, the result was verified with a read.
