---
name: provision-hermes-profiles
description: "Use when creating and configuring Hermes agent profiles."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [synapse, hermes, profiles, providers, provisioning, credentials]
    related_skills: [synapse-architect, hermes-agent, evaluate-agent-skills]
---

# Provision Hermes profiles

Third step of the Architect pipeline (Phase 2): create and configure the
Hermes profile of each validated agent — identity, providers, skills,
credentials — then prove it works. **Only runs after the architecture
plan was explicitly validated.**

## When to use

- Creating the Hermes profile of an agent of the organization.
- Adding/changing the providers, personality or skills of an existing
  profile.
- Verifying that a profile is functional (provider check, LLM test).

## 1. Provider inheritance (hard rule)

Every new profile inherits the **current profile's** model/provider
configuration (the profile that runs this skill — the Architect). Resolve
the current profile dynamically, never a hardcoded name:

```bash
# Current profile (the Architect that is executing this skill)
CURRENT_PROFILE="$(basename "$HERMES_HOME")"
echo "architect profile: $CURRENT_PROFILE"

hermes profile create <agent> --clone-from "$CURRENT_PROFILE" \
  --description "<role> — <organization>"
```

Then verify and complete:

1. `hermes profile show <agent>` — the Model line MUST be the current
   profile's (`deepseek/deepseek-v4-flash-0731 (nous)` for this
   Architect).
2. `auth.json` is present with mode 0600 — if the clone did not copy it
   (it usually does not), copy it from the current profile and chmod 600:
   ```bash
   cp "$HERMES_HOME/auth.json" ~/.hermes/profiles/<agent>/auth.json
   chmod 600 ~/.hermes/profiles/<agent>/auth.json
   ```
3. Run the proof script (also part of the final audit):
   ```bash
   bash scripts/verify_providers.sh <agent> "$CURRENT_PROFILE"
   ```
   It asserts the model/provider match with the parent and performs a
   live LLM query.

An agent without LLM access is a dead agent — never skip this step.

## 2. Personality (SOUL.md)

Rewrite `~/.hermes/profiles/<agent>/SOUL.md` for the role: identity,
mission, responsibilities, tone, working language (French for this
organization), and a reference to the skills the agent owns. Remove the
generic personality left by the clone. Keep it focused (≤ 2-3 KB).

## 3. Skills attribution

From the validated plan and the registry
(`references/skills-registry.md`): remove the skills the agent does NOT
need (the clone brings the whole parent catalog), keep exactly the
validated list. Minimalism is a requirement: a profile with 5 useful
skills beats a profile with 50 unused ones. After attribution, list the
installed skills and compare with the plan.

## 4. Credentials

- Synapse identity: the agent account created with `synapse agent create`
  (two passwords via stdin: agent then org).
- Generate passwords randomly (24+ chars), store in 0600 files, hand
  over as a **sealed handover file** per agent (0600; report the path,
  never the content).
- Tool credentials (API keys etc.): document the intended storage; only
  create them with the user (third-party keys).
- Never pass secrets as CLI arguments; never print them.

## 5. Tests per agent (real proofs)

1. `hermes profile show <agent>` — providers correct.
2. LLM query: `hermes -p <agent> chat -q "Reply with exactly: <AGENT>-OK"`
   — real model answer.
3. Synapse connectivity: login and a test message between two agents
   (`synapse message send ... --my-name <agent> --password-stdin`).
4. Skills present: compare `hermes skills list -p <agent>` (or the
   skills directory) with the validated plan.

## Boundaries

- Never create a profile before validation.
- Never expose secrets (password values, API keys) in commands, logs or
  reports.
- Never reference another profile's private paths in the created
  profile's skills or memory.

## Verification

- [ ] `hermes profile show <agent>`: Model = parent's (proof kept).
- [ ] `auth.json` present, 0600.
- [ ] `verify_providers.sh <agent>` exits 0 with a real LLM answer.
- [ ] SOUL.md rewritten for the role (not the generic clone).
- [ ] Skills = validated plan (no extras).
- [ ] Credentials handed over as 0600 sealed files, paths reported only.
- [ ] Test message between two agents succeeded.
