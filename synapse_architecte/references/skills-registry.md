# Skills registry — canonical catalog for skill attribution

The canonical source used by `synapse-architect` (family
`synapse_architecte/`) to recommend skills for agent roles. Entries are
real, verified Hermes skills. Recommend FROM this registry; never invent
a skill. Extend it only after a validated skill creation
(`create-hermes-skills` + `evaluate-agent-skills`).

Format per entry: `name — capability — credentials — typical roles`.

## Research & intelligence

- `arxiv` — arXiv search by keyword/author/category/ID. None. Research
  analyst, scientific watch.
- `blogwatcher` — monitor blogs and RSS/Atom feeds via blogwatcher-cli.
  None (feeds). Watch/veille roles.
- `polymarket` — Polymarket markets, prices, orderbooks, history. None.
  Market/analyst roles.
- `youtube-content` — YouTube transcripts to summaries/threads/blogs.
  None (public videos). Content analyst.
- `grounded-citations` — ground answers in cited, verifiable sources.
  None. Any analyst producing reports.
- `llm-wiki` — build/query interlinked markdown KB (Karpathy's LLM Wiki).
  None. Knowledge manager.
- `huggingface-hub` — HuggingFace hf CLI: search/download/upload models
  and datasets. HF token for uploads. ML engineer.

## Communication & email

- `himalaya` — IMAP/SMTP email from terminal. Email account creds
  (IMAP/SMTP). Any communication role.
- `google-workspace` — Gmail, Calendar, Drive, Docs, Sheets via gws CLI
  or Python. Google OAuth. Administrative/assistant roles.
- `teams-meeting-pipeline` — Teams meeting summaries, job replay, Graph
  subscriptions. Microsoft Graph creds. Meeting/PM roles.
- `imessage` — send/receive iMessages/SMS via imsg CLI (macOS). macOS +
  Apple account. Personal assistant (macOS).
- `xurl` — X/Twitter via xurl CLI: search, posting, DM, media. X API
  creds. Social media manager.

## Productivity & documents

- `docx` — create/read/edit Word documents. None. Any role producing
  reports.
- `xlsx` — create/read/edit Excel spreadsheets and CSVs. None. Finance,
  ops, data roles.
- `powerpoint` — create/read/edit PPTX decks. None. Presentation roles.
- `pdf` — create, merge, split, fill, secure PDF files. None. Any role.
- `nano-pdf` — edit text in existing PDFs via natural-language prompts.
  None. Legal, admin.
- `ocr-and-documents` — extract text from PDFs/scans (pymupdf,
  marker-pdf). None. Document-heavy roles.
- `notion` — Notion API + ntn CLI: pages, databases, markdown, Workers.
  Notion token. PM/knowledge roles.
- `obsidian` — read/search/create/edit notes in an Obsidian vault. None.
  Knowledge roles.
- `airtable` — Airtable REST API via curl: records CRUD, filters,
  upserts. Airtable token. Ops/PM.
- `maps` — geocode, POIs, routes, timezones via OSM/OSRM. None.
  Logistics, field roles.

## Software development & technical

- `synapse-project` — Synapse (A2A) project context, CLI, conventions.
  Synapse account. Any agent working with Synapse (dependency).
- `github-repo-management` — clone/create/fork repos, remotes, releases.
  GitHub token. Developer roles.
- `github-pr-workflow` — PR lifecycle: branch, commit, open, CI, merge.
  GitHub token. Developer roles.
- `github-code-review` — review PRs: diffs, inline comments. GitHub
  token. Developer roles.
- `github-issues` — create, triage, label, assign issues. GitHub token.
  Developer/PM roles.
- `codebase-inspection` — inspect codebases with pygount: LOC, languages,
  ratios. None. Technical audit roles.
- `systemd-service-deployment` — author/test systemd units. Root for
  install. DevOps role.
- `load-testing` — load-test a server: real RPS, latency, saturation.
  Target access. DevOps/QA.
- `security-audit` — security audit of code: vuln checklist, PoC proof,
  report. Repo access. Security role.
- `python-argparse-cli` — build multi-level argparse CLIs. None.
  Developer roles.
- `test-driven-development` — TDD RED-GREEN-REFACTOR. None. Developers.
- `spike` — throwaway experiments to validate an idea. None. Technical
  roles.

## Data, ML & evaluation

- `evaluating-llms-harness` — lm-eval-harness benchmarks (MMLU, GSM8K…).
  Compute. ML engineer.
- `weights-and-biases` — W&B experiment tracking, sweeps, registry.
  W&B token. ML engineer.
- `serving-llms-vllm` — vLLM high-throughput serving, quantization.
  GPU. ML ops.
- `llama-cpp` — local GGUF inference + HF Hub discovery. GPU optional.
  ML engineer.

## Monitoring & operations

- `progress-scorecards` — progress reports: real % from verified
  evidence. None. Any PM/audit role.
- `hermes-cron-jobs` — Hermes cron: no_agent watchdogs, schedule
  pitfalls. None. Ops roles.
- `hermes-cron-monitoring` — build Hermes cron watchdogs/scorecards.
  None. Ops roles.
- `agent-mission-monitoring` / `agent-session-monitoring` — monitor
  agent missions/sessions (progress %, stagnation). None. Orchestrator
  roles.

## Media & creative

- `gif-search` — search/download GIFs from Tenor via curl + jq. None.
  Marketing/social.
- `songsee` — audio spectrograms/features (mel, chroma, MFCC). None.
  Audio roles.
- `architecture-diagram` — dark-themed SVG architecture diagrams as
  HTML. None. Technical documentation roles.
- `ascii-art` — pyfiglet/cowsay/boxes/image-to-ascii. None. Fun/marketing.
- `excalidraw` — hand-drawn Excalidraw JSON diagrams. None. PM/design.

## Agent skills for Synapse usage (from the public repo `agent-skills/`)

Skills teaching an agent to USE Synapse with its own account: messaging,
tasks, groups, directory, delegation, events — CLI group forms, contracts
executed against a real server, RBAC respected (reserved commands listed
as names-only limits). **Every agent of an organization receives the
Synapse agent-skills** (or the `synapse-project` context) — the
communication backbone of the organization.

## Attribution rules

- Map role → tasks → capabilities → registry entries (see
  `research-select-skills`).
- Minimalism: only the skills that unlock real tasks for THIS role.
- Credentials: list the TYPES required; actual values are created and
  stored with the user (sealed handover, 0600).
- Security: every attributed skill passes `evaluate-agent-skills`
  (RBAC, bypass, escalation).
