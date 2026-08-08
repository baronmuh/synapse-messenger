# Architecture plan — standardized presentation format

Presented by the Architect (skill `present-validate-plan`) before any
execution. All 8 sections are mandatory; "none" is an acceptable value.
No secret values anywhere (types only).

---

## 1. Identified needs

- Organization: <name> — mission: <one sentence>
- Domain: <activity>
- Objectives (measurable): <…>
- Roles derived: <role — need it answers>
- Tasks the organization must be able to accomplish: <…>

## 2. Proposed Hermes profiles

| Profile | Role | Mission | Responsibilities | Tasks |
|---|---|---|---|---|
| <agent> | <role> | <mission> | <owns> | <verifiable tasks> |

(One row per agent; no decorative agents.)

## 3. Recommended skills (or to create)

| Profile | Skill (registry) | Capability | Deps | Credentials (types) | Source / status |
|---|---|---|---|---|---|
| <agent> | <skill> | <what it unlocks> | <…> | <types only> | registry / verified / GAP+proposal |

GAP entries (skills to create) are listed explicitly with their
justification and remain proposals until validated.

## 4. Permissions and access levels (least privilege)

| Profile | Can do | Cannot do (reserved) | Org visibility |
|---|---|---|---|
| <agent> | <allowed actions> | <reserved names only> | <scope> |

## 5. Tools / providers required

| Profile | LLM provider | Tools | Tool credentials (types) |
|---|---|---|---|
| <agent> | inherited (same as Architect) | <tools> | <types only> |

## 6. Risks and constraints

- <security notes, dependencies, compliance, operational limits>

## 7. Assumptions

- <every hypothesis taken during analysis; each one is challengeable>

## 8. Implementation steps (Phase 2 order)

1. <instance check / installation proposal>
2. <organization creation>
3. <agent creation>
4. <credentials (sealed handover)>
5. <profile provisioning + provider inheritance>
6. <skill attribution / creation (validated gaps)>
7. <tests per agent>
8. <final audit + report>

---

## Validation block (HARD)

> **Validation required.** Please confirm this plan explicitly by
> answering: **I approve this** — or tell me what to change.

The Architect treats ONLY an explicit acceptance as validation. Vague
answers ("do what you want", "you decide", "OK") are refused with a
re-ask. A modified plan requires a new explicit acceptance.
