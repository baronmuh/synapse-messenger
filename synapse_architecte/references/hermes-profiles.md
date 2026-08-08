# Hermes profiles — verified commands (reference)

Profiles live in `~/.hermes/profiles/<name>/` with the same layout as
the home (config.yaml, .env, SOUL.md, skills/, memories/, auth.json…).

```bash
hermes profile list                        # all profiles + model
hermes profile show <name>                 # Model/Provider, gateway, skills
hermes profile create <name> --clone-from <source> --description "..."
hermes profile delete <name> --yes
hermes -p <name> chat -q "..."             # one-shot query with the profile
hermes -p <name> --tui                     # interactive TUI
hermes config set <key> <value> -p <name>  # settings (never hand-edit YAML)
```

Provider facts (verified on this Architect):

- The clone copies config.yaml (incl. the `model.*` section), .env,
  SOUL.md and the skills — but NOT auth.json (the API-key store).
- After cloning, copy `auth.json` from the parent and chmod 600, then
  verify with `hermes profile show` (Model line must match) and a live
  query.
- Secrets live in auth.json / .env, settings in config.yaml — never mix.

Pitfalls:

- A profile without auth.json has "no API keys yet" — it may inherit
  shell env keys, which is NOT a real provider setup.
- `--clone-from` brings the whole skill catalog: strip to the validated
  list (minimalism).
- Never reference another profile's private paths from a provisioned
  profile's skills or memory.

Full Hermes knowledge: `hermes-agent` skill.
