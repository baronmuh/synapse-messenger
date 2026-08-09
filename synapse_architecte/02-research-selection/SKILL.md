---
name: research-select-skills
description: "Use when researching and selecting skills for a role."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [synapse, research, skills, selection, registry, mapping]
    related_skills: [synapse-architect, understand-org-needs, evaluate-agent-skills]
---

# Research & select skills

Second step of the Architect pipeline: for every business role derived by
`understand-org-needs`, research the domain deeply and map its real needs
to **verified skills from the registry**
(`references/skills-registry.md` of the `synapse_architecte` family).
The goal is to understand THE TRADE, not to search "<domain> AI skills".

## When to use

- Before finalizing the skill list of any business agent.
- Before deciding that a new skill must be created (gap analysis).

## 1. Deep research — UNLIMITED budget, structured output

For each business domain, research (web, official docs, recognized
sources) the six dimensions:

- **A. Core competencies** — the real skills of the trade (not clichés).
- **B. Tools** — professional tools practitioners actually use.
- **C. Workflows** — repetitive/complex tasks that can be automated or
  supported by an AI agent.
- **D. Frameworks & standards** — recognized methodologies, standards,
  regulations.
- **E. Reference sources** — the reliable sources an agent should use to
  work correctly.
- **F. Existing skills** — the registry (canonical) + `skill_view` on
  installed skills, and `agent-skills/` of the public Synapse repo.

There is **no budget limit** on research (user decision). Organize the
synthesis: ≤ 1 page per agent in the plan.

## 2. Mapping needs → skills (anti-hallucination)

For each workflow/competency identified, find the matching skill in the
**registry**. Rules:

- A skill is proposed ONLY if it is in the registry (or verified by
  `skill_view` when installed). Never invent a skill.
- Do not propose a skill because its name "sounds related": verify that
  it actually provides the capability.
- Prefer the existing skill over creating a new one.
- A real gap (capability needed, nothing in the registry) is reported as
  a **gap** in the plan, with a creation proposal — validated by the
  user before any authoring.

## 3. Selection criteria (critical review)

For every proposed skill, document:

- why it is needed (which task/competency it unlocks);
- the capability it brings;
- its dependencies (other skills, tools, services);
- required credentials (types only);
- why it beats the alternatives (including "no skill at all").

Eliminate: redundant skills, useless skills, obsolete tools,
popularity-based choices, excessive dependencies, overly broad
permissions.

## 4. Output

The **skill recommendation table** (per agent): skill name (registry
entry), capability, tasks unlocked, dependencies, credentials, rationale,
and the gap list (if any). This feeds the architecture plan validated by
the user.

## Quality bar

- Every recommendation is traceable to a researched need.
- Zero invented skills.
- Synthesis structured and bounded per agent.
- Gaps explicit (not silently ignored, not silently created).

## Verification

- [ ] Research done on all six dimensions for every business role.
- [ ] Every proposed skill exists in the registry (or verified).
- [ ] Rationale documented per skill (why / capability / dependencies /
      credentials / alternative).
- [ ] Gaps listed in the plan.
