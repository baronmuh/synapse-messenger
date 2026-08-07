# SKILLS_AGENT — Skills Hermes pour utiliser le projet Synapse

This folder contains **Hermes Agent**-format skills that let an
AI agent immediately understand and use the Synapse project (a
multi-agent server: messaging, tasks, groups, organizations), from the
only **its account credentials**.

**Separation of responsibilities**: these skills only teach the
features available to an **agent** account (messaging, tasks,
groups, directory, delegations). They contain **no instruction**
for actions reserved for humans or administrators (creating
comptes ou d'agents, administration of'organisations, modification des
permissions/politiques/budgets, audit, observateurs, administration du
server): these actions are listed only as **limits** never to
cross, without any call details. No bypass of
authentication or authorization is suggested nor documented.

All examples were **executed and verified against a real server**
(v3.1.1) : formes CLI en groupes (`synapse message send …`), contrats du
Python client (66 methods), UUIDv4 identifiers, runtime permissions.

## Contenu

| Path | Role |
|---|---|
| `synapse/SKILL.md` | **Root skill (entry point)** — connection with the credentials, permission model (limits), strict security rules, map of allowed commands, pitfalls. Load first. |
| `synapse/references/api-reference.md` | The commands available to your account: permissions, CLI (group) / Python client signatures, verified examples (sections 1-7); reserved commands listed without call details (section 8). |
| `synapse/references/messaging.md` | Messaging scenarios (send, read, unread, notifications, conversations). |
| `synapse/references/tasks.md` | Task scenarios (create, list, states, approvals, transfers, work queue). |
| `synapse/references/groups.md` | Group scenarios (create, members, messages) — CLI by name, Python by UUID. |
| `synapse/references/org-and-agents.md` | What an agent sees of its organization (directory, card, delegations) + **absolute limits**: actions reserved for humans/org, never to attempt. |

## Format (Hermes Agent compliance)

Chaque skill respecte les conventions Hermes :

- frontmatter YAML : `name` (minuscules, tirets, ≤ 64 car.), `description`
  (≤ 1024 chars, self-contained trigger in the first 57 characters),
  `version`, `author`, `license`, `metadata.hermes.{tags, related_skills}` ;
- structure : `# Titre` → `## Overview` → `## When to Use` → corps → `## Common Pitfalls` → `## Verification Checklist` ;
- bulky material in `references/` (progressive disclosure);
- no duplication: authentication and the permission model live
  une seule fois, dans `synapse/SKILL.md`.

## Giving the skills to an agent

1. Copy the `synapse/` folder to the agent's skills directory
   Hermes (par exemple `~/.hermes/profiles/<profil>/skills/`), ou fournir le
   content of `SKILLS_AGENT/` as an attachment/context.
2. Provide the **agent account credentials in the task
   instruction** (account name + password) — never in an environment
   environment variable nor as a command argument (the project's
   security rule). Also indicate the socket path if different from the default
   (`/var/run/synapse/synapse.sock`) : la configuration se passe par
   `--config <chemin>` ou `$SYNAPSE_CONFIG`.
3. The agent loads `synapse` (the skill triggers on "Synapse project"),
   verifies its connection (`synapse api get_my_organization --my-name <account>
   --password-stdin`), puis utilise les commandes selon les droits de son
   account (the skill documents precisely what an agent account can and cannot
   peut pas faire).

## Prerequisites

- The project installed: the `synapse` command in the PATH (installed
  Python package) and/or the importable package (`python -c "import synapse"`).
- The Synapse server started and reachable via the Unix socket (default
  `/var/run/synapse/synapse.sock`).
- An existing account: agent (`--my-name`) or human
  (`<org>_humain`), selon les permissions voulues.
