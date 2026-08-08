---
name: skill-template
description: "Template for new Hermes-compatible skills (fill, don't keep placeholders)."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [synapse, template]
    related_skills: [synapse-architect, create-hermes-skills, evaluate-agent-skills]
---

# <Skill name — one clear purpose>

<One paragraph: what the agent achieves with this skill, when it applies,
and what it does NOT cover. Trigger conditions first.>

## When to use

- <concrete trigger 1>
- <concrete trigger 2>

## 1. <Step — imperative, numbered>

<Exact commands, real flags, verified by execution. Never from memory.>

```bash
<command that was actually run and worked>
```

<Explain expected output and how to react to errors.>

## 2. <Next step>

...

## Pitfalls

1. <mistake to avoid — learned from execution, not theory>
2. <...>

## Verification

- [ ] <check that proves the skill's job is done>
- [ ] <...>
