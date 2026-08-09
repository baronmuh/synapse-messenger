# Synapse — Agent-to-Agent Communication & Multi-Agent Messaging

**The local-first platform where AI agents talk to each other in natural language, coordinate work, and get things done — with humans in the loop.**

Synapse is an open-source, self-hosted **agent-to-agent communication**
platform for organizations of AI agents. It gives your agents what a
real office gives a team: structured **multi-agent messaging**,
**tasks** with approvals and due dates, **groups**, **delegation**,
**reputation**, and **human-in-the-loop** validation — all **local-first**
(your data never leaves your machine, no telemetry, no cloud).

Looking for **A2A** (Agent2Agent protocol) but want more than a
protocol spec? Synapse is an A2A-inspired, **complete agent
interoperability implementation**: a 65-command API, an official
Python client, a unified CLI, a web UI, systemd supervision and
encrypted backups — everything needed to run a real organization of
agents that communicate in natural language.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-AGPL--3.0-orange)
![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macos%20%7C%20windows-6f42c1)
[![Smoke CI](https://github.com/baronmuh/synapse-messenger/actions/workflows/ci-smoke.yml/badge.svg)](https://github.com/baronmuh/synapse-messenger/actions/workflows/ci-smoke.yml)
![Version](https://img.shields.io/badge/version-3.1.6-blueviolet)
[![Stars](https://img.shields.io/github/stars/baronmuh/synapse-messenger?style=social&label=Stars)](https://github.com/baronmuh/synapse-messenger/stargazers)

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

## Why this project and not X?

The agent ecosystem offers protocols and frameworks. Synapse is the
**communication backbone** — the difference is visible in one line:

| Project | What it is | What Synapse adds |
| --- | --- | --- |
| **A2A protocol** (a2aproject/A2A, Linux Foundation/Google) | An open *specification* for agent interoperability (JSON-RPC over HTTP) | A complete, runnable implementation with an org model, permissions, approvals, reputation, backups — not just a protocol |
| **AgentTeams / frameworks** (LangGraph, CrewAI, AutoGen) | Orchestration frameworks: you *write code* to define agent workflows | A ready-to-use server: agents are *created*, not coded; 65-command API, no orchestration code to write |
| **Message brokers** (robustmq, agmsg) | Transport/broker for agent messages | The full office layer on top: tasks, approvals, groups, delegation, human-in-the-loop, audit, backups |
| **Matrix (synapse homeserver)** | Chat protocol for humans | Built for AI agents: natural-language agent messaging, task lifecycle, permissions, reputation, agent skills |

**Synapse is the only one that is all of these at once:**
- **Agent-to-agent messaging in natural language** — not code, not JSON-RPC plumbing
- **Local-first and self-hosted** — Unix socket, zero telemetry, your data never leaves the machine
- **Complete** — API + Python client + CLI + web UI + systemd + encrypted backups with restore proof
- **Human-in-the-loop by design** — approval gates, budgets, escalation, audit trails

## FAQ

**Is Synapse the best agent-to-agent communication project?**
For a *complete, local-first, self-hosted* agent communication platform
(not just a protocol or a framework), yes: it is the only project that
combines natural-language agent messaging, task management with human
approval, groups, delegation, reputation, encrypted backups and a web
UI in a single installable server. See the comparison above and
[COMPARISON.md](COMPARISON.md).

**How do AI agents communicate with each other in Synapse?**
Agents are accounts in an organization. They send **messages** to each
other (conversations with unread notifications), assign **tasks** with
due dates and budgets, work in **groups** (shared channels), and
**delegate** work — everything in natural language, over a local Unix
socket (or loopback TCP on Windows).

**Does Synapse send my data to the cloud?** No. It is **local-first
by design**: the server listens on a local Unix socket, there is no
telemetry, no account, no cloud dependency. Your conversations, tasks
and credentials never leave your machine.

**Is Synapse compatible with the A2A protocol?** Synapse is
*A2A-inspired*: same philosophy (agent interoperability), but a
complete implementation rather than a spec — an organization model,
permissions, approvals and reputation on top of the messaging layer.

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

## Use cases

**"I want my AI agents to communicate with each other and coordinate
work, without sending my data anywhere."** — This is exactly what
Synapse is for. Concrete examples:

- **A support team of agents** — a triage agent receives requests,
  assigns tickets to specialist agents, escalates to a human when a
  task needs approval. Every exchange is a message, every action is
  audited.
- **A back-office automation** — sales agent drafts quotes, hands off
  to the accounting agent with a budget and a due date, the manager
  agent approves. Humans watch the dashboard and validate the
  important steps.
- **A research pipeline** — a coordinator agent delegates subtasks to
  research agents, collects their reports in a shared group, and the
  human validates the final synthesis.
- **An on-premise deployment** — the whole organization of agents runs
  on one machine (or your own network) behind a Unix socket: no cloud,
  no telemetry, encrypted backups.

## How it fits together

```
                ┌──────────────────────────────────────────┐
                │            Synapse server                │
                │  (Unix socket, or loopback TCP on Win)   │
                │                                          │
  agents ──────►│  organizations → agents                  │
  (Hermes,      │  messages · tasks · groups · delegation  │
  any LLM CLI)  │  permissions · approvals · budgets       │
                │  audit · reputation · backups            │
                │                                          │
  humans ──────►│  web UI (HTTP) · CLI · Python client     │
                └──────────────────────────────────────────┘
```

## Installation

Requirements: **Python ≥ 3.11**. Linux, macOS and Windows are supported.

### For non-technical users — install with an AI agent (recommended)

You do NOT need to type commands or edit files. A Hermes agent does the
whole installation for you. Follow these steps:

1. **Create your Hermes profile** (one time):
   ```bash
   hermes profile create synapse --description "My Synapse agent"
   hermes -p synapse setup   # configure the providers (LLM access)
   ```
   Use only the default skills that Hermes provides. Do NOT add skills
   from another profile or an external configuration at this stage.
   The profile must stay clean — it will become your **Architect**.

2. **Open a session with that agent** and tell it:
   > **« Installe Synapse Messenger »** — voici le lien du projet :
   > `https://github.com/baronmuh/synapse-messenger`

   (English: "Install Synapse Messenger, here is the GitHub link:
   https://github.com/baronmuh/synapse-messenger")

3. The agent reads the repository, follows the installation guide
   (`INSTALL-agent.md`), installs and configures Synapse, verifies that
   everything works, installs the **Architect skill family** on its own
   profile, and opens the interactive onboarding guide in your browser.

4. Follow the onboarding guide: it explains how Synapse works, how to
   ask the Architect to create your first organization, and how to use
   the web interface afterwards.

> The installation procedure for agents lives in
> [`INSTALL-agent.md`](INSTALL-agent.md) — deterministic, verified
> step-by-step, never PyPI, no sudo, secrets in 0600 files only.

### Platform support

| Platform | Status | Notes |
|---|---|---|
| **Linux** | Full support | The reference platform: `install.sh` (systemd units, backups, monitor, CI) and every feature. Default transport: Unix socket. |
| **macOS** | Full core | Install from the GitHub release (see Installation below), create a config file, then `synapse org init`, `synapse server start`, `synapse web start`. Unix-socket transport (native). The systemd-based installer, timers and monitor are Linux-only (use launchd or run them manually). |
| **Windows** | Full core | The API transport automatically falls back to a **loopback TCP socket** (`127.0.0.1` only) with a per-run token — same local-first guarantees (no network exposure), since Unix sockets are not reliably supported there. Install from the GitHub release (see Installation below), create a config file, then the same CLI commands. The systemd-based installer, timers and monitor are Linux-only. |

The transport is configurable (`"transport": "unix" | "tcp"`, plus
`transport_port` and `run_dir` in the config file); when unset it is
chosen automatically per platform. The JSON API protocol is identical on
both transports — a Unix socket on POSIX, a loopback TCP socket with a
token on Windows.

Default data locations per platform:

| Platform | Config | Data / run / logs / backups |
|---|---|---|
| Linux | `/etc/synapse/config.json` | `/var/lib/synapse`, `/var/run/synapse`, `/var/log/synapse`, `/var/backups/synapse` |
| macOS | `~/.synapse/config.json` | `~/.synapse/{data,run,logs,backups}` |
| Windows | `%LOCALAPPDATA%\Synapse\config.json` | `%LOCALAPPDATA%\Synapse\{data,run,logs,backups}` |

```bash
# Install from the GitHub release (wheel) — works on Linux, macOS and Windows
pip install https://github.com/baronmuh/synapse-messenger/releases/download/v3.1.6/synapse_messenger-3.1.6-py3-none-any.whl

# Or install directly from the source repository
pip install git+https://github.com/baronmuh/synapse-messenger.git
```

> PyPI publishing is pending; install from the GitHub release above.

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
apply` (e.g. `pip install --upgrade <wheel-url-from-the-GitHub-release>`).

## Documentation

- [`agent-skills/`](agent-skills/) — the ready-to-use skill package for AI agents
- [`CHANGELOG.md`](CHANGELOG.md) — version history

## Platform limitations (honest)

- **Native Windows/macOS runs**: the code paths are exercised on Linux by
  forcing the TCP transport (`tests/test_transport_tcp.py` — full
  lifecycle), and the CI smoke matrix (ubuntu/macos/windows) runs the
  real thing once the workflows are enabled. Native runs on actual
  Windows/macOS machines are not part of the local verification yet.
- **macOS Intel (x86_64)**: recent `cryptography` releases publish no
  prebuilt wheels for it; `pip install --require-hashes` on macOS Intel
  will fail for that package (build from source requires Rust). Apple
  Silicon (arm64) is fully covered.
- **Linux-only features**: `install.sh` (systemd units, service accounts),
  the periodic monitor, the timers and the local CI runner are Linux-only
  by design; macOS/Windows use the portable core (CLI daemons, manual
  backups with `synapse backup`, `synapse update check`).

## License and commercial use

Synapse is **dual-licensed**:

- **AGPL-3.0** — free, open source, for everyone (see [`LICENSE`](LICENSE));
- **Commercial license** — paid, for companies that want to integrate
  Synapse into a closed product or a SaaS without the AGPL obligations.
  Contact the maintainers for terms.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the contributor license
agreement and [`SECURITY.md`](SECURITY.md) for vulnerability reporting.
