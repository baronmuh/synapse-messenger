# SKILLS_AGENT — Hermes skills for using the Synapse project

This folder contains **Hermes Agent**-format skills that let an
AI agent immediately understand and use the Synapse project (a
multi-agent server: messaging, tasks, groups, organizations), using
only **its account credentials**.

**Separation of responsibilities**: these skills only teach the
features available to an **agent** account (messaging, tasks,
groups, directory, delegations). They contain **no instruction**
for actions reserved for humans or administrators (creating
accounts or agents, administration of organizations, modification of
permissions/policies/budgets, audit, observers, administration of the
server): these actions are listed only as **limits** never to
cross, without any call details. No bypass of
authentication or authorization is suggested nor documented.

All examples were **executed and verified against a real server**
(v3.1.1): CLI forms in groups (`synapse message send …`), contracts of the
Python client (66 methods), UUIDv4 identifiers, runtime permissions.

## Contents

| Path | Role |
|---|---|
| `synapse/SKILL.md` | **Root skill (entry point)** — connection with the credentials, permission model (limits), strict security rules, map of allowed commands, pitfalls. Load first. |
| `synapse/references/api-reference.md` | The commands available to your account: permissions, CLI (group) / Python client signatures, verified examples (sections 1-7); reserved commands listed without call details (section 8). |
| `synapse/references/messaging.md` | Messaging scenarios (send, read, unread, notifications, conversations). |
| `synapse/references/tasks.md` | Task scenarios (create, list, states, approvals, transfers, work queue). |
| `synapse/references/groups.md` | Group scenarios (create, members, messages) — CLI by name, Python by UUID. |
| `synapse/references/org-and-agents.md` | What an agent sees of its organization (directory, card, delegations) + **absolute limits**: actions reserved for humans/org, never to attempt. |

## Format (Hermes Agent compliance)

Each skill respects the Hermes conventions:

- YAML frontmatter: `name` (lowercase, hyphens, ≤ 64 chars), `description`
  (≤ 1024 chars, self-contained trigger in the first 57 characters),
  `version`, `author`, `license`, `metadata.hermes.{tags, related_skills}`;
- structure: `# Title` → `## Overview` → `## When to Use` → body → `## Common Pitfalls` → `## Verification Checklist`;
- bulky material in `references/` (progressive disclosure);
- no duplication: authentication and the permission model live
  only once, in `synapse/SKILL.md`.

## Giving the skills to an agent

1. Copy the `synapse/` folder to the agent's Hermes skills directory
   (for example `~/.hermes/profiles/<profil>/skills/`), or provide the
   content of `SKILLS_AGENT/` as an attachment/context.
2. Provide the **agent account credentials in the task
   instruction** (account name + password) — never in an environment
   variable nor as a command argument (the project's
   security rule). Also indicate the socket path if different from the default
   (`/var/run/synapse/synapse.sock`): the configuration is passed via
   `--config <path>` or `$SYNAPSE_CONFIG`.
3. The agent loads `synapse` (the skill triggers on "Synapse project"),
   verifies its connection (`synapse api get_my_organization --my-name <account>
   --password-stdin`), then uses the commands according to the rights of its
   account (the skill documents precisely what an agent account can and cannot
   do).

## Prerequisites

- The project installed: the `synapse` command in the PATH (installed
  Python package) and/or the importable package (`python -c "import synapse"`).
- The Synapse server started and reachable via the Unix socket (default
  `/var/run/synapse/synapse.sock`).
- An existing account: agent (`--my-name`) or human
  (`<org>_humain`), according to the desired permissions.
