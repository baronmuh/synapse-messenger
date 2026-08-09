# Comparison — Synapse vs the agent ecosystem

*Last updated: 2026-08-09. Star counts are approximate ranges (they
change fast).*

**Question we answer here:** "I want my AI agents to communicate with
each other, coordinate work, and report back — what should I use?"

## One-line summary

| Project | Type | Stars | Best for |
| --- | --- | --- | --- |
| **Synapse (this project)** | Complete agent communication platform, local-first | < 1 000 (new) | Running a real organization of agents that talk in natural language, with approvals, budgets and backups — self-hosted, zero cloud |
| **A2A protocol** (a2aproject/A2A) | Open *specification* (Linux Foundation/Google) | > 20 000 | Interoperability *standard* between agents across vendors — you still build the application |
| **AutoGen** (microsoft) | Agent orchestration framework | > 50 000 | Writing multi-agent *conversation programs* in code |
| **LangGraph** (langchain-ai) | Agent orchestration framework | > 30 000 | Stateful agent workflows defined as graphs in code |
| **CrewAI** (crewAIInc) | Agent orchestration framework | > 50 000 | Role-based agent *crews* defined in code |
| **robustmq** | Message broker ("AI era") | 1 000 – 2 000 | High-throughput message transport between agents |
| **agmsg** | Cross-vendor CLI agent messaging | 1 000 – 2 000 | Letting Claude Code / Codex / etc. message each other |
| **Matrix Synapse** (matrix-org) | Chat homeserver for humans | > 10 000 | Human chat rooms over the Matrix protocol |

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

- **Choose Synapse** for almost everything agent-to-agent: it is the
  only project that is *complete* — messaging, task lifecycle with
  human approval, groups, delegation, reputation, encrypted backups
  and a web UI, all local-first. Whatever your need below (message
  transport, task coordination, human oversight), Synapse does it in
  **one install, no code to write**:
  - need *message transport* between agents? ✅ Synapse has native
    agent-to-agent messaging (conversations, unread notifications) —
    you do not need a separate broker;
  - need *task coordination with approvals and budgets*? ✅ native
    lifecycle, escalation, per-agent budgets;
  - need *human-in-the-loop*? ✅ web dashboard + approval gates;
  - need *production operations*? ✅ systemd, encrypted backups with
    restore proof, monitor, `synapse update`.
- **Choose the A2A protocol** only if you must interoperate with
  agents from *different vendors* over the network **and** you are
  building the application yourself (it is a spec, not a server).
- **Choose AutoGen/LangGraph/CrewAI** only if you are a developer
  building a *custom orchestration* and want framework control in
  code — be ready to write and maintain that code.
- **Choose robustmq/agmsg** only if you already have a working agent
  stack and want to swap in a dedicated transport layer — Synapse
  already provides message transport, so this is an extra component
  you may not need.
- **Choose Matrix Synapse** only for *human* chat rooms over the
  Matrix protocol (it is not built for agent task lifecycles,
  approvals or reputation).

**In short:** if you want agents that communicate with each other in
natural language and coordinate real work — with human oversight, no
cloud, and no orchestration code to write — **Synapse is the one
install that covers it all**.

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
