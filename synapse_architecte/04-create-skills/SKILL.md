---
name: create-hermes-skills
description: "Use when creating new Hermes-compatible skills."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [synapse, skills, authoring, SKILL.md, frontmatter]
    related_skills: [synapse-architect, evaluate-agent-skills, hermes-agent-skill-authoring]
---

# Create Hermes-compatible skills

Fourth step of the Architect pipeline: author new skills that are
directly usable by Hermes agents, respecting the format and conventions.
Only runs after the gap was validated in the plan (see
`research-select-skills`). **A created skill is delivered only after it
passes the security gate of `evaluate-agent-skills`.**

## When to use

- A validated gap requires a capability no existing skill provides.
- Improving/rewriting an existing skill (same process).

## 1. Format — SKILL.md

- File: `SKILL.md` at the root of the skill directory
  (`skills/<category>/<skill-name>/SKILL.md` in a profile, or a
  subdirectory of the `synapse_architecte` family in the repo).
- Frontmatter (YAML):
  ```yaml
  ---
  name: <lowercase-hyphen, ≤ 64 chars>
  description: "Use when <trigger>. <one-line behavior>."  # ≤ 60 chars,
    # trigger first, ends with a period — the system prompt index truncates
    # longer ones to 57 chars and loses the routing signal
  version: 1.0.0
  author: Hermes Agent
  license: MIT
  platforms: [linux, macos, windows]
  metadata:
    hermes:
      tags: [synapse, ...]
      related_skills: [synapse-architect, ...]
  ---
  ```
- Body: trigger conditions → numbered steps with exact commands →
  pitfalls → verification checklist. Target 8-15k chars; use
  `references/`, `templates/`, `scripts/` for detail (progressive
  disclosure) instead of one monolith.

## 2. Conventions

- Progressive disclosure: umbrella + references per domain, never a
  >20k monolith, never many sibling skills duplicating auth.
- English for anything that ships publicly; French is fine for internal
  working skills of this family (the Architect's user works in French).
- Exact commands, verified by execution — never examples from memory
  (signatures, flags, env vars drift; see the pitfalls below).
- A `templates/` subfolder with a ready-to-fill template, a `scripts/`
  subfolder for proof/audit scripts.

## 3. Factual validation (mandatory)

Before shipping a skill, EXECUTE what it documents:

1. `--help` dump for every CLI command cited.
2. Run every example against the real system (a live Synapse instance, a
   real Hermes profile).
3. Verify the environment variables a skill mentions actually exist
   (grep the source); never invent credential conventions
   (secrets on stdin, not env vars, for Synapse).
4. Verify the frontmatter with the Hermes validator (name/description
   budgets, size range).

## 4. Security gate (mandatory before delivery)

Run `evaluate-agent-skills` on the new skill:

- RBAC separation respected (agent-facing skills document only what the
  account can do; reserved actions appear as names-only limits, with NO
  signatures or examples).
- No bypass instructions, no escalation hints, no admin tools.
- `ACCESS_DENIED` documented as expected behavior.
- Grep audit passes (reserved-command calls = 0).

## Boundaries

- Never create a skill outside the validated plan.
- Never document reserved/administrative actions in agent-facing skills.
- Never embed secrets in a skill.

## Verification

- [ ] Frontmatter valid (name ≤ 64, description ≤ 60, size in range).
- [ ] Every command/example executed against the real system.
- [ ] Security gate (`evaluate-agent-skills`) passed.
- [ ] Skill placed in the right category and listed in the registry
      (after user validation of the new entry).
