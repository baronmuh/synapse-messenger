---
name: evaluate-agent-skills
description: "Use when auditing security of agent-facing skills."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [synapse, security, rbac, audit, agent-facing, escalation]
    related_skills: [synapse-architect, create-hermes-skills, agent-facing-skill-authoring]
---

# Evaluate & secure agent-facing skills

Fifth step of the Architect pipeline — the **security and quality gate**.
It evaluates any skill destined to agents: RBAC correctness, permission
bypasses, privilege escalation, cross-skill interactions, and risk
analysis. It also shapes skills from their design so that security is
built in, not bolted on. Runs continuously (on every proposed/created
skill) and as part of the final audit.

## When to use

- Before delivering any skill created by `create-hermes-skills`.
- Before attributing any skill from the registry to an agent.
- During the final audit of a provisioned organization.

## 1. RBAC verification

For a skill targeting a restricted account, verify:

- The skill documents ONLY what the account type can do.
- Reserved/human/admin actions appear as a **names-only limits list**
  ("never attempt"), with NO signatures, parameters, call examples, or
  admin tools — grep the skill: `reserved_cmd(` calls must be 0.
- Permission families are documented from the real dispatch tables /
  runtime checks, not from an older doc (lists drift).

## 2. Bypass and escalation analysis

Check and document:

- **Bypass instructions**: any hint to circumvent authentication or
  authorization, fake an identity, or use another account's credentials
  → FAIL.
- **Privilege escalation**: any path where a documented action could
  reach an admin/reserved capability (chained commands, raw API access
  with elevated args, `--force` flags on reserved commands) → FAIL.
- **Credential hygiene**: env-var credential conventions invented for a
  tool that does not support them; secrets in examples or arguments
  (`--password`); secrets on stdin requirement violated → FAIL.
- **Cross-skill interactions**: two skills whose combination could
  bypass a control (e.g. a read skill feeding a reserved command's
  arguments). List the interactions analyzed.
- **Error semantics**: `ACCESS_DENIED` documented as expected behavior
  (not a bug), no "workaround" instructions.

## 3. Risk analysis output

Produce a per-skill verdict table:

| Skill | RBAC ok | Bypass | Escalation | Credentials | Interactions | Verdict |
|---|---|---|---|---|---|---|
|  | pass/fail | none/found | none/found | ok/leak | analyzed | APPROVE / FIX / REJECT |

Verdicts:
- **APPROVE** — no issue; deliver/attribute.
- **FIX** — concrete issues to correct in the skill; loop back to
  `create-hermes-skills`.
- **REJECT** — design is unsafe (bypass/escalation/secret leak); do not
  deliver.

## 4. Security by design

When authoring or improving a skill, integrate security at design time:

- Document the permission boundary in the skill's intro.
- Embed explicit no-bypass rules in the body.
- State that a refusal is expected behavior and the skill confers no
  extra privileges.
- Keep the reserved-command list regenerated from the live dispatch
  tables.

## Automation

Run `scripts/audit_rbac.sh <skill-directory>` — the grep audit for
reserved-command calls, admin tools, and credential patterns. It must
exit 0 (0 occurrences) for agent-facing skills. (Adapt the pattern list
to the project's reserved commands; the Synapse list is maintained in
`synapse-project`.)

## Boundaries

- Never weaken a control to make a skill "work".
- Never propose a bypass as a solution.
- Never document reserved actions with examples "for completeness".

## Verification

- [ ] Grep audit passes (0 reserved-call / admin-tool occurrences).
- [ ] No bypass, no escalation path, no secret leak found.
- [ ] Cross-skill interactions analyzed and listed.
- [ ] Verdict table produced (APPROVE / FIX / REJECT).
- [ ] `ACCESS_DENIED` documented as expected behavior where relevant.
