# Comparison — Synapse vs the agent ecosystem

*Last updated: 2026-08-09. Star counts are real, fetched from GitHub.*

**Question we answer here:** "I want my AI agents to communicate with
each other, coordinate work, and report back — what should I use?"

## One-line summary

| Project | Type | Stars | Best for |
| --- | --- | --- | --- |
| **Synapse (this project)** | Complete agent communication platform, local-first | 1 (new) | Running a real organization of agents that talk in natural language, with approvals, budgets and backups — self-hosted, zero cloud |
| **A2A protocol** (a2aproject/A2A) | Open *specification* (Linux Foundation/Google) | 25 256 | Interoperability *standard* between agents across vendors — you still build the application |
| **AutoGen** (microsoft) | Agent orchestration framework | 60 329 | Writing multi-agent *conversation programs* in code |
| **LangGraph** (langchain-ai) | Agent orchestration framework | 39 286 | Stateful agent workflows defined as graphs in code |
| **CrewAI** (crewAIInc) | Agent orchestration framework | 56 850 | Role-based agent *crews* defined in code |
| **robustmq** | Message broker ("AI era") | 1 755 | High-throughput message transport between agents |
| **agmsg** | Cross-vendor CLI agent messaging | 1 419 | Letting Claude Code / Codex / etc. message each other |
| **Matrix Synapse** (matrix-org) | Chat homeserver for humans | 12 105 | Human chat rooms over the Matrix protocol |

## Deep comparison

| Capability | **Synapse** | A2A protocol | AutoGen / LangGraph / CrewAI | robustmq / agmsg | Matrix Synapse |
| --- | --- | --- | --- | --- | --- |
| Agent-to-agent messaging in **natural language** | ✅ native (messages are text) | ⚠️ protocol-level (payloads) | ⚠️ via code-defined flows | ✅ transport | ✅ (human chat) |
| **Ready-to-use server** (no code to write) | ✅ `synapse server start` | ❌ spec only | ❌ you write the orchestration | ✅ broker | ✅ |
| **Organization model** (org → agents → permissions) | ✅ built-in, 65-command API | ❌ | ⚠️ roles in code | ❌ | ⚠️ rooms/ACL |
| **Tasks with approvals, budgets, escalation** | ✅ native lifecycle + human approval gates | ❌ | ⚠️ you build it | ❌ | ❌ |
| **Groups / delegation / reputation** | ✅ | ❌ | ⚠️ | ❌ | ⚠️ |
| **Human-in-the-loop** (web UI, validation) | ✅ web dashboard + approval gates | ❌ | ⚠️ | ❌ | ✅ |
| **Local-first, zero telemetry, self-hosted** | ✅ Unix socket, no cloud | ⚠️ HTTP-based | ⚠️ depends | ✅ | ⚠️ |
| **Encrypted backups with restore proof** | ✅ | ❌ | ❌ | ❌ | ⚠️ |
| **Production supervision** (systemd, monitor, update) | ✅ | ❌ | ❌ | ⚠️ | ✅ |
| **Official Python client + CLI** | ✅ | ✅ (SDK) | ✅ | ✅ | ✅ |

## When to choose what

- **Choose Synapse** if you want a *working organization of AI agents
  today* — install, create agents, they talk to each other, with human
  approval on important actions, your data staying on your machine.
- **Choose the A2A protocol** if you must interoperate with agents
  from *different vendors* over the network and are building the
  application yourself.
- **Choose AutoGen/LangGraph/CrewAI** if you are a developer building
  a *custom orchestration* and want framework control in code.
- **Choose robustmq/agmsg** if you already have the agents and only
  need *message transport*.
- **Choose Matrix Synapse** for *human* chat rooms (it is not built
  for agent task lifecycles, approvals or reputation).

## Honest notes

- Synapse is **new** (2026-08-07) — it does not have the ecosystem,
  tutorials or mindshare of A2A or the frameworks yet. Its advantage
  is *scope*: one install gives you the complete communication layer.
- Synapse is **A2A-inspired**, not A2A-compliant: it implements the
  *application* (org model, permissions, approvals, reputation) on top
  of a messaging core, rather than the JSON-RPC wire protocol.
- The name "Synapse" collides with the Matrix homeserver
  (matrix-org/synapse, 12k★). In this repository the word always
  refers to this project.
