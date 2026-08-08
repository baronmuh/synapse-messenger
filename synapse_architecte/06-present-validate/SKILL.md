---
name: present-validate-plan
description: "Use when presenting a plan for explicit user validation."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [synapse, architecture, plan, validation, gate, presentation]
    related_skills: [synapse-architect, understand-org-needs, research-select-skills, provision-hermes-profiles, create-hermes-skills]
---

# Present results & get explicit validation

The **gate between analysis and execution**. After understanding the
organization and completing the research, the Architect presents the
complete architecture in a standardized format and **obtains an EXPLICIT
acceptance from the user** — nothing is created, provisioned or
configured before that acceptance. This skill defines the presentation
format and, above all, the validation protocol.

## When to use

- Always, between Phase 1 (design/research) and Phase 2 (provisioning).
- Whenever the plan changes after user feedback (re-present the updated
  plan and re-validate).

## 1. The standardized presentation format

Use `templates/architecture-plan.md` — every section is mandatory. The
plan presents, in this order:

1. **Identified needs** — synthesis of the organization analysis
   (mission, objectives, roles derived, tasks).
2. **Proposed Hermes profiles** — per agent: role, mission,
   responsibilities, tasks it will perform.
3. **Recommended skills (or to create)** — per agent, from the registry
   (or verified); creations marked as GAP + proposal.
4. **Permissions and access levels** — least-privilege RBAC per agent,
   who sees/does what.
5. **Tools / providers required** — types only (never secret values):
   LLM providers, tool credentials needed per agent.
6. **Risks and constraints** — security notes, dependencies, limits.
7. **Assumptions** — every hypothesis taken during the analysis.
8. **Implementation steps** — the ordered execution plan for Phase 2.

Present it clearly (terminal-friendly text, tables), no secrets, no
vague statements. The user must be able to understand, verify and
challenge every line.

## 2. The validation protocol (HARD RULE)

The validation is an **explicit acceptance**, never an implicit or vague
one.

- End the presentation with a clear request, e.g.:

  > **Validation required.** Please confirm this plan explicitly by
  > answering: **J'APPROUVE CE PLAN** — or tell me what to change.

- **Acceptable answers** (explicit acceptance): "J'APPROUVE CE PLAN",
  "I approve", "Validated, go ahead", "Approved" — an unambiguous
  confirmation of the plan.

- **NOT acceptable — the Architect must re-ask, refusing to treat them
  as validation**: vague replies such as "do what you want", "you
  decide", "think for yourself", "as you see fit", "whatever", a simple
  "OK" without reference to the plan, or silence. When the user answers
  vaguely, reply:

  > This is not a validation. Nothing will be created until you
  > explicitly confirm the plan. Please answer **J'APPROUVE CE PLAN**
  > or tell me exactly what to change.

- **Objections / changes**: update the plan accordingly and re-present
  it (back to section 1) — a modified plan requires a NEW explicit
  acceptance.

- Only the explicit acceptance unlocks Phase 2 (provisioning). After
  acceptance, execute the validated plan end-to-end without re-asking
  for each step.

## 3. Separation of phases (never blurred)

```
analysis/research  →  presentation (this skill)  →  user validation  →  execution
      Phase 1                        gate                     ║              Phase 2
                                                         explicit acceptance
```

Any attempt to start creating profiles, skills or configurations before
the explicit acceptance is a violation of the hard rules (umbrella).

## Pitfalls

1. Treating "OK", "do what you want" or "you decide" as validation —
   re-ask explicitly.
2. Presenting without the standardized format (missing sections force
   the user to guess).
3. Including secrets in the plan (never — values stay out, types only).
4. Executing after vague feedback "to save a round-trip" — the gate
   exists for a reason.
5. Re-validating a changed plan with the old acceptance.

## Verification

- [ ] Plan presented in the standardized format, all 8 sections.
- [ ] Explicit acceptance obtained (exact wording or unambiguous
      confirmation).
- [ ] No secret value in the presentation.
- [ ] No creation started before the acceptance.
- [ ] Any plan change re-presented and re-validated.
