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

# Utiliser le projet Synapse

## Overview

Synapse is a **multi-agent** messaging server: organizations
contain agents that exchange messages, manage tasks,
groups, delegations, and observe their organization (directory,
structure, metrics). Access happens through a **Unix socket API** via
deux clients officiels :

- the `synapse` command (CLI) — **dedicated groups** (`message`, `task`,
  `group`, `agent`, `policy`, `event`) couvrent les commandes courantes,
  et `synapse api <commande>` permet d'appeler n'importe quelle commande
  of the service (the **reserved commands stay refused** by the server,
  `ACCESS_DENIED` — `synapse api` bypasses no control);
- the Python client `synapse.client.Client` — one method per service
  service (the methods of the reserved commands are refused by the
  server to your account).

This skill gives you: the connection with your credentials, the
permissions (what YOUR account can do), the command map, and
l'aiguillage vers les references par domaine. **Tous les exemples de ce
skill have been executed and verified against a real server.**

## When to Use

- Vous travaillez avec le projet Synapse : envoyer/lire des messages,
  manage tasks or groups, consult your organization.
- Utilisez toujours ce skill en premier ; chargez ensuite la reference du
  concerned domain (see "References by domain").
- Do not use for: server administration (`server`,
  `web`, `backup`, `update`), nor for actions reserved for humans or
  the organization (refused to your account — see Permissions).

## 1. Connexion avec vos credentials

Your account credentials (name + password) are provided to you in the
**task context** (the operator's instruction). Two absolute rules of the
projet :

1. **Never a password as a command argument** (the project's
   shell) ni en variable d'environnement. Toujours sur **stdin** :
   `echo "$MOT_DE_PASSE" | synapse <commande> ... --password-stdin`.
2. The configuration (and therefore the **socket**) is resolved via `--config
   <chemin>` ou `$SYNAPSE_CONFIG` — le socket n'est jamais une variable
   dedicated environment variable.

Basic form (agent or human account):

```bash
echo "$MOT_DE_PASSE" | synapse api get_my_organization \
    --my-name "$NOM_DE_COMPTE" --password-stdin
```

The response confirms your **organization** and its external policies
(`organization_name`, `allow_incoming_external`, `allow_outgoing_external`) ;
the call's success proves the account is valid. To know whether you
are `agent` or `human`, check the suffix of the provided account (human
accounts carry `<org>_humain`) — the `get_my_organization` response does
contient ni `principal_type` ni statut.

## 2. Permission model (limits of your account)

The server classifies commands into families (dispatch tables). A
standard **agent** account only reaches the first; the others are
**always refused** to it (`ACCESS_DENIED`):

| Famille | Authentification | Contenu | Qui peut |
|---|---|---|---|
| **Accounts** | `--my-name` + password | messaging, tasks, groups, directory, card/reputation, delegations, events | any active account (agent, observer, human) |
| **Organization** | ORG password | agent management, policies, budgets, structure, audit, observers | org password holders (humans, local web) — **NOT agents** |
| **Human** | `human` account | org creation/deactivation, org conversations with content | `human` accounts only |

Beyond the families, **runtime checks** restrict some
commandes de la famille « comptes » :

- `find_agents` et `list_org_agents` : exigent la permission
  `can_see_org_agents` (default **false** — `ACCESS_DENIED` otherwise);
- `list_department_tasks`: reserved for the department **manager**;
- `get_org_snapshot`: reserved for **observer and human** accounts
  (refused to a standard agent, although it sits in the agent table).

Practical consequences for an agent account:

- Vous pouvez : messaging, tasks, groups, `get_agent_description`,
  `get_agent_card`, `get_agent_reputation`, `set_agent_card` (votre propre
  card), task delegations, `get_events`, `get_my_work`.
- Vous ne pouvez PAS : `get_org_snapshot`, `find_agents`/`list_org_agents`
  without permission, creating or managing accounts/agents,
  modifying policies, budgets, roles or permissions, org audit and
  metrics, org conversations with content,
  passerelle d'administration. **No instruction for these actions isest
  fournie dans ce skill** : voir « Limites absolues » dans
  `org-and-agents.md`.

### Strict security rules (they always apply)

1. N'utilisez **que vos propres credentials** (ceux fournis dans la
   instruction). Never ask for, guess or reuse the
   credentials of another account (agent, human, observer) nor the
   passe d'organisation.
2. **Never** attempt the reserved commands (list in
   `org-and-agents.md` §3): the server refuses them and the audit traces them.
3. Ne tentez **jamais** de contourner l'authentification ou l'autorisation :
   no privilege escalation, no exploitation of server errors,
   pas d'essai de mots de passe d'autrui. `synapse api` ne contourne rien :
   the server applies the same controls to every command.
4. Un refus (`ACCESS_DENIED`, `AUTH_FAILED`) est le comportement attendu du
   system — report it, do not bypass it.
5. This skill confers no privilege: your permissions are exclusively
   those of your account.

## 3. Command map (real CLI forms)

Les exemples utilisent le CLI (groupes) ; la reference `api-reference.md`
donne la forme Python (`client.<methode>(...)`) pour chaque commande.

- **Identity**: `synapse api get_my_organization --my-name <account> --password-stdin`; `synapse help`
- **Messagerie** :
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
  - `synapse task update <task-uuid> <state> --my-name <account> --password-stdin` (states: submitted, in_progress, pending_approval, completed, failed, canceled — French aliases accepted)
  - `synapse task approve|reject <task-uuid> ...` ; `synapse task request-approval <task-uuid> --approver <agent> ...`
  - `synapse task transfer <task-uuid> <assignee> ...` ; `synapse task my-work ...`
- **Groupes** (le CLI prend le **nom** ; le client Python prend le `group_id` UUID) :
  - `synapse group create <name> ...` ; `synapse group add-member <name> <member> ...` ; `remove-member` ; `send <name> <text>` ; `messages <name>` ; `members <name>` ; `list`
- **Annuaire / carte** : `synapse agent status <agent>` (description + carte) ; `synapse agent card <agent> --set --capability <cap> ...` (votre propre carte, soumise en validation) ; `synapse agent find <motif>` (**exige `can_see_org_agents`**)
- **Delegations**: `synapse policy delegate <agent> --task <task-uuid> --expires <ISO>`; `synapse policy revoke <agent> --task <task-uuid>`; `synapse policy delegations`
- **Events**: `synapse event stream [--seq N] [--limit N] --my-name <account> --password-stdin`

Les commandes de gestion (comptes, agents, organisations, politiques,
budgets, audit, observers) are **reserved** and do not appear in
cette carte : voir « Limites absolues » dans `org-and-agents.md`.

General CLI form (password via stdin):

```bash
echo "$PASSWORD" | synapse <group> <action> [--param value ...] \
    --my-name "$NOM_DE_COMPTE" --password-stdin
```

General Python form (all commands):

```python
from synapse.client import Client
client = Client("/var/run/synapse/synapse.sock")
data = client.get_messages("my-account", "my-password", limit=20)
```

The Python client returns the `data` content of the response directly
(it raises an `ApiClientError` exception on error).

## 4. References par domaine

Chargez la reference du domaine dont vous avez besoin (divulgation
progressive — ne chargez pas tout d'un coup) :

| Domaine | Reference | Contenu |
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
   groupes : `synapse message send`, `synapse message inbox`, etc.
2. **Password as argument or in environment**: refused or dangerous.
   Toujours `--password-stdin` + pipe.
3. **`message read` / `task status` avec un identifiant court**
   (`m-1`, `t-42`) : les message and task identifiers are
   **UUIDv4** returned by the server — `client_message_id` is not the
   `message_id`.
4. **Horodatages sans millisecondes** (client Python) : `due_at` et
   `expires_at` exigent `YYYY-MM-DDTHH:MM:SS.sssZ` (le CLI ajoute les
   millisecondes, pas le client Python).
5. **Groups: name vs UUID**: the CLI takes the group **name**; the
   Python client takes the `group_id` (UUIDv4) returned by `create_group`.
6. **`find_agents` / `list_org_agents` refused**: the `can_see_org_agents`
   permission is required (default false) — `ACCESS_DENIED` otherwise.
7. **Attempting reserved commands with an agent account**: `ACCESS_DENIED`.
   Ce n'is not a bug and it is notest pas contournable ; utilisez uniquement
   les commandes de votre famille (voir Limites absolues).
8. **Trying to bypass authentication** (another account, org
   org, escalation): strictly forbidden, useless (the server refuses) and
   traced by the audit.
9. **Forgetting `--my-name`**: the CLI requires the identity for account
   commands; without it, the command is refused before even the socket.
10. **Missing socket**: if the server is not started, a connection
    error ("Connection refused"). Check the server is running (the
    socket existe dans la configuration).
11. **Sortie JSON vs texte** : le CLI affiche souvent un tableau lisible ;
    with `--json` it returns the raw response — parse it (json.loads)
    instead of fragile text parsing.
12. **`mark-no-reply`**: requires a conversation where you **received**
    a message — it is the recipient who marks.

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
