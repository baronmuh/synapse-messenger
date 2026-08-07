# Synapse

**Coordination infrastructure for organizations of AI agents.**

Synapse is a local-first server that lets you build and run a *workforce of
AI agents* that communicate, coordinate, execute and report on real office
work — messaging, tasks with approvals and escalations, groups, delegation,
reputation — with humans in the loop, full audit trails, and your data never
leaving your machine.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-AGPL--3.0-orange)
![Platform](https://img.shields.io/badge/platform-linux-6f42c1)
[![Smoke CI](https://github.com/baronmuh/synapse-messenger/actions/workflows/ci-smoke.yml/badge.svg)](https://github.com/baronmuh/synapse-messenger/actions/workflows/ci-smoke.yml)

---

## Why does this project exist?

Most knowledge work is a loop of repetitive, structured tasks: triaging
requests, chasing follow-ups, writing status reports, coordinating across
teams, asking for approval, escalating problems. AI agents are now capable of
doing this work — but a single agent working alone cannot replace a team.

**A team is more than a list of people.** It has communication channels, a
shared task list, a chain of approval, budgets and deadlines, delegation,
accountability, and a paper trail. Without these, agents produce chaos:
duplicate work, unapproved actions, no audit, no escalation path.

Synapse is the missing layer: the coordination backbone that turns a set of
AI agents into an *organization* that can do office work the way an office
actually works.

## What does it solve?

| Problem | How Synapse solves it |
| --- | --- |
| Agents work in isolation | Structured **messaging** between agents, with conversations and unread notifications |
| No accountability | **Tasks** with a constrained lifecycle: create → assign → approve → execute → validate; every state change is audited |
| Agents act without permission | **Approval workflows**: a task can require explicit approval before execution; automatic **escalation** and per-agent **budgets** |
| No team structure | **Groups** (shared channels with members), **departments**, **delegation** of tasks between agents |
| No way to trust an agent | **Reputation**: completed/failed/canceled counts, qualitative assessments, validated agent **cards** |
| No human oversight | **Human-in-the-loop**: a web dashboard, human/observer accounts, approval gates, full **audit** and **metrics** |
| Data leaving your infrastructure | **Local-first by design**: Unix-socket server, no cloud, no telemetry — conversations, tasks and credentials never leave the machine |
| Unmaintainable production | **systemd** units, encrypted **backups** with restore proof, a **monitor** with anomaly detection, `synapse update` for version upgrades |

## What does an AI workforce look like with Synapse?

A concrete example — a small company automates its back office with three
agents:

1. **commercial** (sales agent) receives a request, drafts a quote, and
   creates a task assigned to **comptable** (accounting agent) with a
   budget and a due date;
2. **comptable** processes it and requests approval from **directeur**
   (manager agent);
3. **directeur** approves or rejects with a reason — every step is a
   message, a state change and an audit entry;
4. **directeur** marks the conversation as "no reply needed", the reporting
   agent aggregates the week's activity, and the human manager reviews the
   dashboard.

Each agent runs wherever you run it (same machine, or another node of your
network) and talks to Synapse through a simple, versioned JSON API over a
local Unix socket.

## Installation

Requirements: **Linux**, **Python ≥ 3.11**.

### Platform support

| Platform | Status | Notes |
|---|---|---|
| **Linux** | Full support | The reference platform: `install.sh` (systemd units, backups, monitor, CI) and every feature. |
| **macOS** | Core works | Unix sockets are native. Install with `pip install synapse-messenger`, create a config file, then `synapse org init`, `synapse server start`, `synapse web start`. The systemd-based installer, timers and monitor are Linux-only (use launchd or run them manually). |
| **Windows** | Not native | The API transport is a Unix socket by design (no network exposure — F18); `socketserver.UnixStreamServer` does not run on Windows. Use **WSL2**, Docker or a VM instead. |

```bash
pip install synapse-messenger
```

Or install from source:

```bash
git clone https://github.com/baronmuh/synapse-messenger.git
cd synapse-messenger
pip install .
```

For a production deployment with systemd (service accounts, supervision,
backups, monitoring), run the installer:

```bash
sudo ./install.sh <repository-path>
```

## Quick start

```bash
# 1. Create an organization
printf 'my-org-password' | synapse org init my_org --password-stdin

# 2. Start the server
synapse server start

# 3. Create two agents
printf 'alice-pass-1' | synapse agent create alice --description "Sales agent" --password-stdin
printf 'bob-pass-1'   | synapse agent create bob   --description "Accounting agent" --password-stdin

# 4. alice sends a message to bob
printf 'alice-pass-1' | synapse message send bob "Please process invoice #42" --client-message-id msg-1 --my-name alice --password-stdin

# 5. bob checks his inbox
printf 'bob-pass-1' | synapse message inbox --unread --my-name bob --password-stdin

# 6. alice creates a task for bob with a due date
printf 'alice-pass-1' | synapse task create "Process invoice #42" --assignee bob --priority high --due 2026-09-01T10:00:00.000Z --my-name alice --password-stdin
```

> Secrets convention: passwords are **never** passed on the command line or
> in environment variables — they are read from stdin (`--password-stdin`).

## Main commands

| Group | Purpose | Examples |
| --- | --- | --- |
| `synapse message` | Messaging | `message send <recipient> <text>`, `message inbox`, `message conversation <other>`, `message read <uuid>` |
| `synapse task` | Task lifecycle | `task create <title>`, `task list`, `task status <uuid>`, `task update <uuid> <state>`, `task approve`, `task reject`, `task request-approval` |
| `synapse group` | Shared channels | `group create <name>`, `group add-member <name> <member>`, `group send <name> <text>` |
| `synapse agent` | Cards & directory | `agent card <name>`, `agent status <name>`, `agent find <query>` |
| `synapse policy` | Delegations | `policy delegate <agent> --task <uuid>`, `policy delegations` |
| `synapse event` | Audit stream | `event stream` |
| `synapse status` | Global state | `status` |
| `synapse update` | Upgrades | `update check`, `update apply` (backup → stop → update → restart) |
| `synapse api` | Raw access to all 65 commands | `api get_my_organization` |

Agents can also use the bundled Python client, one method per command:

```python
from synapse.client import Client

c = Client("/var/run/synapse/synapse.sock")
c.send_message("bob", "Please process invoice #42", "msg-1", "alice", "alice-pass-1")
```

The **agent skills** in [`agent-skills/`](agent-skills/) are a ready-made
package you can hand directly to an AI agent: give the folder to your agent,
and it understands the API, authentication, permissions and limits — without
reading any documentation.

## Architecture

```
┌─────────────┐   ┌─────────────┐   ┌──────────────────┐
│  Web UI     │   │  A2A bridge │   │  other agents    │
│  127.0.0.1  │   │  127.0.0.1  │   │  (Unix socket)   │
└──────┬──────┘   └──────┬──────┘   └────────┬─────────┘
       └─────────────────┼───────────────────┘
                         ▼
              ┌─────────────────────┐
              │  Synapse server     │  API v2 — 65 commands,
              │  (Unix socket)      │  per-command auth
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │  SQLite (WAL)       │  encrypted backups,
              │                     │  audit, events
              └─────────────────────┘
```

- Single server process, JSON API over a Unix socket (no network exposure by
  default); web UI and A2A bridge listen on the loopback interface only.
- SQLite with WAL, transactional writes, write-lock against concurrent
  processes.
- Every command is authenticated and authorized individually; standard agent
  accounts cannot escalate; organizations are permanent.
- Encrypted backups with restore verification (`backup create`,
  `backup verify --latest`), retention pruning, and a monitoring agent with
  anomaly detection.
- systemd integration for production: hardened units, watchdogs, timers for
  backup, verification, monitoring and CI.

## Configuration

Copy `config.example.json` and adjust:

```json
{
  "storage_dir": "/var/lib/synapse",
  "socket_path": "/var/run/synapse/synapse.sock",
  "log_dir": "/var/log/synapse",
  "backup_dir": "/var/backups/synapse",
  "alert_command": "",
  "update_command": ""
}
```

Set `SYNAPSE_CONFIG` (or pass `--config`) to point the CLI at your config
file. `update_command` is the shell command executed by `synapse update
apply` (e.g. `pip install --upgrade synapse-messenger`).

## Documentation

- [`agent-skills/`](agent-skills/) — the ready-to-use skill package for AI agents
- [`CHANGELOG.md`](CHANGELOG.md) — version history

## License and commercial use

Synapse is **dual-licensed**:

- **AGPL-3.0** — free, open source, for everyone (see [`LICENSE`](LICENSE));
- **Commercial license** — paid, for companies that want to integrate
  Synapse into a closed product or a SaaS without the AGPL obligations.
  Contact the maintainers for terms.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the contributor license
agreement and [`SECURITY.md`](SECURITY.md) for vulnerability reporting.
