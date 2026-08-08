---
name: understand-org-needs
description: "Use when analyzing an organization's needs and roles."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [synapse, architecture, requirements, analysis, org-design]
    related_skills: [synapse-architect, research-select-skills, provision-hermes-profiles]
---

# Understand organization needs

First step of the Architect pipeline: turn a user request into concrete,
structured requirements for Hermes agent profiles. **Nothing is created
while using this skill** — its output is the input of the architecture
plan.

## When to use

- The user asks to create an organization or add agents to one.
- The request is a mission statement ("an organization specialized in
  cybersecurity that does monitoring, incident analysis, audits and
  reports") with implicit role implications.

## 1. Structured collection

Collect (ask targeted questions when missing) the following dimensions —
use `templates/needs-collection.md` as the working sheet:

| Dimension | Questions |
|---|---|
| Organization | name, mission, domain of activity |
| Objectives | what the organization must achieve, success criteria |
| People & roles | employees, their roles and responsibilities (the humans and/or agents the profiles will serve) |
| Work processes | main workflows, recurring tasks, deliverables |
| Operational needs | constraints (compliance, language, hours, budget), tools already in use |
| Capabilities required | the tasks the organization must be able to accomplish |

Do not jump to agents: understand the business first. If the request is
vague, ask 2-5 targeted questions BEFORE designing.

## 2. Role derivation (dynamic, never a fixed list)

From the needs, derive the roles — examples such as management, research,
finance, legal, marketing, sales, technical, cybersecurity, DevOps are
*illustrations*, not a menu. For each candidate role, document:

- role name and mission;
- responsibilities (what it owns);
- why it is necessary (which need it answers);
- tasks it will perform (concrete, verifiable);
- required competencies;
- skills candidates (defer the final selection to
  `research-select-skills`).

**Eliminate decorative agents**: a role without a real, non-overlapping
scope is dropped. If two roles overlap, merge or split explicitly with a
justification.

## 3. Requirements output

Produce the **requirements document** (template in
`templates/needs-collection.md`): organization facts, role table, task
matrix (role × task), constraints, and open questions for the user.
This document is the input of the architecture plan presented for
validation.

## Quality bar

- Every requirement is traceable to a stated need (no invented
  objectives).
- Roles are derived from the analysis, never from a template list.
- Ambiguities are resolved by asking, not by guessing.

## Verification

- [ ] Collection sheet filled for all dimensions.
- [ ] Every role mapped to a stated need (why it exists).
- [ ] No decorative role.
- [ ] Open questions listed for the user.
