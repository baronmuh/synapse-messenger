# Research method — the six dimensions

Working reference for `research-select-skills`. For every business
domain, gather evidence on all six dimensions before proposing anything.

## A. Core competencies

The fundamental competencies of the trade. Sources: professional bodies,
curricula, job descriptions, recognized practitioners. Output: a list of
competencies with a one-line justification of why each is core.

## B. Tools

The professional tools practitioners use. Sources: vendor docs, market
surveys, community consensus. Output: tool list with typical use and
licensing notes. An AI agent should be able to work with these tools or
produce artifacts compatible with them.

## C. Workflows

Repetitive or complex tasks that can be automated or assisted. Sources:
process documentation, case studies, practitioner interviews. Output:
workflow list with automation potential (high/medium/low) and the agent
capability that would cover it.

## D. Frameworks & standards

Methodologies, standards, regulations of the domain. Sources: official
standard bodies, regulatory texts, recognized frameworks. Output:
framework list with the compliance obligations the agent must respect.

## E. Reference sources

The reliable sources an agent should consult: official documentation,
authoritative repositories, recognized databases. Output: source list
with usage (primary/secondary).

## F. Existing skills

The canonical registry (`references/skills-registry.md`) + `skill_view`
on installed skills + `agent-skills/` of the public Synapse repo.
Output: the mapping table needs → registry entries, and the explicit gap
list.

## Synthesis format (per agent, ≤ 1 page in the plan)

```
Role: <role>
Domain research: A/B/C/D/E summarized in 5 bullets
Skill mapping:
  | Skill (registry) | Capability | Tasks unlocked | Deps | Credentials | Why this one |
  |---|---|---|---|---|---|
Gaps (if any): <capability needed, nothing in registry — creation proposal>
```
