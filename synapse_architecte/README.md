# synapse_architecte

Skill family for the **Synapse Organization & Agent Architect** — a
specialized Hermes agent that designs, configures and evaluates Hermes
profiles for the agents of an organization, then provisions them in
Synapse.

## Family layout (pipeline order)

| Path | Skill | Purpose |
|---|---|---|
| `synapse-architect/` | **umbrella** | role, hard rules, pipeline, dispatch |
| `01-understand-needs/` | understand-org-needs | analyze an organization, roles, processes → requirements |
| `02-research-selection/` | research-select-skills | deep domain research (unlimited) + needs → verified skills |
| `03-provision-profiles/` | provision-hermes-profiles | create/configure profiles: providers, SOUL, skills, credentials, tests |
| `04-create-skills/` | create-hermes-skills | author Hermes-compatible skills (format, conventions, validation) |
| `05-evaluate-secure/` | evaluate-agent-skills | security & quality gate: RBAC, bypass, escalation, cross-skill risks |
| `06-present-validate/` | present-validate-plan | standardized plan presentation + **explicit user validation gate** (vague answers are refused) |
| `references/skills-registry.md` | **registry (canonical)** | role → skills catalog for attribution (user decision 4b) |
| `references/synapse-cli.md` | reference | verified Synapse CLI commands |
| `references/hermes-profiles.md` | reference | verified Hermes profile commands |

## Pipeline

understand (01) → research & select (02) → present + **explicit
validation** (06 — vague answers are NOT validation) → provision (03) →
create skills if validated gap (04) → evaluate & secure (05, gate for
04) → final audit with real proofs.

Hard rules (umbrella): validation before creation; anti-hallucination
(registry only); secrets never in clear (0600 sealed handover); least
privilege; provider inheritance for every created profile; never via
PyPI; real proofs only.

## Installation

Install the family into the Architect profile (the profile that will
run the workflow — resolved dynamically, no fixed name):

```bash
cp -r synapse_architecte "$HERMES_HOME/skills/"
```

The umbrella is the entry point of the family.

## Status

- Published in this repository (`synapse_architecte/`).
- All content in English.
