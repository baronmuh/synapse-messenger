# Changelog

All notable changes to the Synapse project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project follows [SemVer](https://semver.org/).

## [3.1.7] — 2026-08-14

### Fixed

- **Test-suite stability under parallel load** — the CLI start timeouts
  (`server` / `web` / `a2a`, previously hardcoded 15s) and the client
  socket read timeout (previously 10s) are now env-configurable
  (`SYNAPSE_START_TIMEOUT`, `SYNAPSE_SOCKET_TIMEOUT`). Production
  defaults are unchanged; parallel test workers get headroom, so
  intermittent "service unavailable" / "Internal error" failures under
  memory pressure are gone.
- **`require_service` and `diag doctor` socket probes** retry briefly
  (1s total) — closes the bind→listen race of a freshly started daemon
  (a single probe could false-FAIL a healthy service).
- **Default pytest parallelism pinned to `-n 3`** — `-n auto` can
  exhaust RAM on 4-core / 8GB machines.
- **`seed_demo.py` supports `SYNAPSE_FAST_HASH=1`** (test harness only)
  — demo seeding is ~4× faster; production Argon2id parameters are
  unchanged.

## [Unreleased] — 2026-08-12 (causal time — HLC primitive, C1)

### Added

- **Hybrid Logical Clock (C1)** — the first causal-time primitive of
  Synapse (DESIGN_CAUSAL_TIME_HLC_v2): one `hlc` column on
  `events` / `task_events` / `audit_log` plus one merge rule, so the
  journal answers "what is provably before what", not just "what does
  the wall clock say". `at` stays untouched (humans, UI, retention
  purge); `hlc` is additive proof. Semantics mirror the **CockroachDB
  HLC reference contract** (wall-first comparison with logical
  tiebreak, atomic updates, persisted upper bound — the process clock
  rehydrates from `MAX(hlc)` at boot, so it never moves below what has
  been durably written). YugabyteDB's HybridTime corroborates the
  design; CockroachDB remains the byte-level contract. There is no IETF
  HLC standard (verified absence): Synapse defines the de-facto
  agent-facing spec.
  - Canonical encoding `"{l:013d}.{c:06d}"` (13 digits of physical ms,
    6 digits of logical counter) makes SQLite TEXT byte order equal to
    causal order. One stamp per write transaction (I4: within an
    instance, hlc order == seq order). Server-stamped only — never
    accepted from clients.
  - `events` also gains `prev_event` (exact DAG edge, populated
    forward-only) and the Events API (`get_events`, `synapse event
    stream`) exposes `hlc` + `prev_event` per event. Malformed hlc
    strings are rejected at the API boundary; `create_task` accepts an
    optional `{agent, hlc}`-typed `deadline` value at the boundary
    WITHOUT semantics (phase-2 seam for causal deadlines and
    deadline-driven admission control).
  - The A2A bridge attaches the Synapse hlc to outbound envelopes as an
    extension field (ignored by non-Synapse peers) and observes remote
    envelope hlc before processing — the merge rule runs through the
    real transport, exercised by the two-instance MVE with a +30 s
    clock skew.
  - **History caveat (H8):** backfilled hlc on pre-C1 databases is a
    *best-effort causal reconstruction* — monotone and consistent with
    `at`, but NOT a proof of cross-instance order (no cross-instance
    traffic existed before the primitive). Do not claim proofs for
    history.
  - **Privacy note (phase-2, out of scope):** HLC stamps reveal causal
    order to any reader of the journal; envelope-level decoy/camouflage
    and erasure-safe causal structures are phase-2 privacy concerns
    (P3/P4), out of scope for C1.
  - **IT2-D hook (for the D-1 builder):** when fact digests gain an hlc
    field, the acceptance gate runs `clock.observe(fact.hlc)` before
    journal insert, and digest reconciliation compares by hlc (causally
    consistent, not arrival-consistent). MANDATORY: the IT2-D
    `fact_hash` canonicalization follows RFC 8785 (JCS) and MUST treat
    IEEE-754 negative zero ("-0") as an ERROR input (verified technical
    erratum eid7920); validate against the cyberphone conformance
    vectors; record JCS as an Informational RFC used as a de-facto
    standard.
  - Not cited as HLC precedent (verified counter-examples): TiDB
    (central TSO, not HLC), Cassandra (client timestamps + LWW, not
    HLC), MongoDB (unconfirmable), Longhorn (no evidence), the PyPI
    "hlc" package (a hosts(5) converter, not HLC).

## [3.1.6] — 2026-08-09 (uninstall command, simplified update)

### Added

- **`synapse uninstall`** — complete uninstallation of Synapse (mirror of
  `install.sh`): stops and removes the systemd units and timers, removes
  the service account, the configuration (`/etc/synapse`), the data
  (`/var/lib/synapse`), run, logs and backups (paths from the
  configuration or the platform defaults), then uninstalls the Python
  package and the `synapse` command. macOS: `~/.synapse`; Windows:
  `%LOCALAPPDATA%\Synapse`. Options: `--dry-run` (shows the plan,
  removes nothing), `--keep-data` (preserves data and backups),
  `--yes` (confirms without the interactive prompt; stops the running
  services cleanly first). Refuses while the server is running unless
  `--yes` is given. On Linux the root-requiring parts re-run with sudo
  (or print the exact command when sudo is unavailable).
- **`synapse update`** — check + apply in one step for a non-technical
  operator. If a new version is available: backup → stop → update →
  restart, then confirms the new installed version. If already up to
  date: clear message, exit 0, no action. Options: `--check-only`
  (equivalent to `update check`), `--yes`, `--dry-run`, `--no-backup`.
  Reuses the internal logic of `update check`/`update apply` (no
  duplicated implementation).

## [3.1.5] — 2026-08-09 (friction reduction — single sources of truth)

### Changed (behavior preserved)

- **Version**: new `synapse/version.py` reads `pyproject.toml` (installed
  metadata first, source checkout fallback). A version bump now touches
  exactly ONE file (`pyproject.toml`) — no more `_FALLBACK_VERSION`.
- **Error messages**: all `ApiError` calls use named constants in
  `synapse/errors.py` (40 contextual variants + the `_MESSAGES` dict).
  Changing a message = editing ONE file.
- **Code duplication**: identical `_level_int` (3 copies) → shared
  `level_int()` in `cli/common.py`; `_pid_alive` wrappers → direct
  `common.pid_alive`.

## [3.1.4] — 2026-08-09 (refactorisation, performance audit & onboarding fixes)

### Fixed (onboarding flow — deep analysis, real-server proofs)

- **`/login` route**: with 0 organization, `/` redirects to `/onboarding`,
  and the onboarding buttons pointed to `/` → infinite loop: the
  create-org form was unreachable during installation. Added an
  explicit `/login` route (served without the onboarding gate) and
  pointed the onboarding buttons there.
- **Onboarding theme tokens**: the page used undefined CSS variables
  (`--bg`, `--ink`, `--card`…) instead of the real `--color-*` tokens —
  the design-system theme never applied. Fixed to the actual tokens.
- **Lockout exemption for the local web identity**: the failure lockout
  (`_authenticate`) also applied to `_WEB_LOCAL` — a few mistyped
  passwords made first-organization creation impossible ("Too many
  failed attempts"). `_WEB_LOCAL` (local 0600 trust token) is now
  exempt; human accounts keep the anti-bruteforce protection.
- **Note on "401 session required"**: verified by inspecting the
  published wheels — v3.1.1/v3.1.2 lack the `/onboarding` route and
  `onboarding.html`; v3.1.3 serves `/onboarding` 200 without a session
  (proven by fresh-venv install of the published wheel). Upgrading to
  ≥ v3.1.3 fixes the 401.

### Changed (refactorisation — behavior preserved)

- **Single source of truth for the version**: new `synapse/version.py`
  reads `pyproject.toml` (installed metadata first, source checkout
  fallback). The hard-coded `_FALLBACK_VERSION` in cli/common.py is
  gone — a version bump now touches exactly ONE file (pyproject.toml).
- **Centralized error messages**: all 60 `ApiError(code, "hard-coded
  message")` calls now use named constants defined in
  `synapse/errors.py` (40 contextual variants + the `_MESSAGES` dict).
  Changing a message = editing ONE file.
- **Dead code removed**: unused imports (service.py `os`/`secrets`,
  cli/common.py `signal`/`socket`, store/tasks.py
  `TASK_DEPENDENCY_NOT_MET`, client.py `_platform` local import,
  web.py `sys`, systemd_notify.py `time`), unused locals
  (cli/diag.py `info`, cli/web.py `state`), a redundant f-string in
  cli/agent.py and a trivial indirection wrapper in service.py
  (`queries_row_to_message_as_of` → direct `messages.row_to_message_as_of`).
- **Duplication eliminated**: the identical `_api_error` helper (7
  copies: agent, event, group, message, org, policy, task) is now one
  shared `api_error()` in cli/common.py; the daemon config-path
  resolution (`_config_arg`, 2 copies: a2a, web) is one shared
  `config_arg_path()` in cli/common.py; the identical `_level_int`
  (3 copies: server, a2a, web) is one shared `level_int()`; the
  `_pid_alive` wrappers (diag, web) now call `common.pid_alive`
  directly.
- **Fixed**: a latent `NameError` in `update` `_a2a_cli_restart`
  (called an undefined `_default_paths()` whenever
  `SYNAPSE_SECRETS_DIR` was unset — the default case). Now falls back
  to `platform.default_paths()`; regression test added.
- **i18n consistency**: the last remaining French user-facing error
  message ("Budget de messages horaire atteint") is now
  "Hourly message budget exceeded".

### Performance audit (measure → identify → fix → verify → guard)

- Profiled on a realistic dataset (3000 messages, 2000 tasks, 500
  group messages): every hot query already uses an index
  (EXPLAIN QUERY PLAN); connection pooling per thread is already in
  place (documented in db.py). Candidate indexes for
  `list_org_conversations` and `get_org_audit` measured neutral or off
  the real query path → **reverted, none kept** (per the
  measure-first rule).
- **Guard added**: `test_hot_query_indexes_present` protects the
  indexes the hot queries rely on against future schema changes.

## [3.1.3] — 2026-08-08 (workflow audit & full English)

### Fixed

- **Onboarding guide**: the `/onboarding` route and the `/` →
  `/onboarding` redirect (when no organization exists yet) are now
  served by the web server — previously the page existed but the route
  was missing on the published branch, producing a "session required"
  error.
- **UI bug**: the Tasks tab counter used a French route (`taches`) that
  never matched the router (`tasks`) — the counter now works.
- **Login error message**: "session requise" → "session required"
  (English, matches the actual API).
- **100% English**: all French docstrings, comments, user-facing error
  messages and UI labels were translated to English across the codebase
  (Python service/CLI, web UI, CSS comments, install.sh, agent skills,
  onboarding guide, tests). API contracts, command names, JSON field
  names and error codes are unchanged.

## [3.1.2] — 2026-08-08 (cross-platform support)

### Added

- **Cross-platform support (Linux, macOS, Windows)**: the API transport is
  now an abstraction — a Unix socket on POSIX (unchanged default) and a
  **loopback TCP socket** (`127.0.0.1` only) with a per-run token
  (`<run_dir>/transport.token`, 0600) on Windows, where
  `socketserver.UnixStreamServer` is not reliable. The JSON API protocol,
  CLI and web UI are identical on both transports.
- **Platform-aware defaults**: config/data/run/log/backup directories per
  OS (Linux keeps `/var|/etc`; macOS uses `~/.synapse`; Windows uses
  `%LOCALAPPDATA%\Synapse`). New config fields: `transport`,
  `transport_port`, `run_dir` (all optional).
- **Portable process control**: `os.kill(pid, 0)` probes replaced by a
  handle-based check on Windows; graceful stop uses SIGTERM on POSIX and
  CTRL_BREAK_EVENT (SIGBREAK handler) on Windows; detached daemons spawn
  with `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` on Windows.
- **UTF-8 console output** on every platform (`ensure_utf8_stdio`).
- **Multi-platform dependency lock**: `pip-compile --generate-hashes`
  already records every distribution hash published on the index, so
  `pip install --require-hashes` works on Linux, macOS and Windows from
  the same `requirements.lock` (verified for win_amd64 and
  macosx_12_0_arm64 wheels).
- **CI matrix workflow** (`.github/workflows/ci-smoke.yml`): smoke tests on
  ubuntu-latest, macos-latest and windows-latest.

### Tested

- New `tests/test_transport_tcp.py` forces the TCP transport on Linux and
  exercises the full lifecycle (org init, server, CLI, Python client, web
  proxy, wrong-token rejection, clean shutdown) — the Windows code path is
  genuinely covered without a Windows machine.
- Full suite: 977 tests green (Linux). Native Windows/macOS runs are
  exercised by the CI matrix when the workflows are enabled.

## [Unreleased] — 2026-08-07 (documentation + test performance)

### Documented

- **Documentation centralization**: single `docs/` hub (index `docs/README.md`) —
  `specs/` (SPEC.txt, SPEC_WEB.txt, SPEC_CLI, ARCHITECTURE, CONFORMITE),
  `production/`, `securite/`, `webui/`, `perf/`. README.md rewritten (links,
  977 tests, unified CLI).
- **Removed obsolete documents** (13 outdated phase/mission reports,
  duplicates, rejected v2 design, `prompt1.txt` mission brief): their useful
  conclusions are merged into SECURITY.md and PERFORMANCE.md. Git history
  keeps them.
- **TESTING.md rewritten** (65 test files, port/systemd isolation,
  parallelism); **TEST_PERFORMANCE.md** (before/after measurements).

### Improved

- **Parallelized test suite** (`pytest-xdist`): 16 min 16 s → 5 min 60 s on
  4 cores (`-n 3`), same 977 tests, 0 failure, ~99% coverage unchanged — no
  loss of isolation or realism (details and rationale:
  `docs/perf/TEST_PERFORMANCE.md`).
- `scripts/ci.sh` runs the suite in parallel by default
  (`SYNAPSE_CI_WORKERS`); `pytest-cov` and `pytest-xdist` in the `[dev]`
  extras + regenerated `requirements.lock`.

## [3.1.1] — 2026-08-07 (production audit)

### Fixed

- **Update tests and DOM harness made reliable on a production machine**:
  `SYNAPSE_NO_SYSTEMD=1` (documented variable) forces the CLI mode of
  `update apply` — real systemd units no longer switch the tests to
  `systemctl`; the DOM harness now waits for the server socket before
  starting the web (startup race eliminated).
- **CLI `agent status`**: reputation display reads the real server contract
  (`completed/failed/canceled/active/completion_rate` counters for self,
  `qualitative` mention for others) — the old phantom
  `score/total_reviews` contract always showed "— (0 reviews)".
- **`set_escalation_policy` validation**: `due_after_seconds` /
  `failed_after_seconds` thresholds are integers >= 1; `null` (converted to
  0 by the old validation) triggered immediate escalation of all tasks —
  now `INVALID_ARGUMENT`.
- **helpdoc**: `get_org_agents` documents the real `principal_type` and
  `reputation`; `get_org_snapshot` documents observer **and** human access.
- **Tests**: reachability of the 17 error codes (5 v3 codes added to the
  global test); observer whitelist locked against real read commands;
  `agent status` reputation test; `null`/`0` escalation threshold rejection;
  removed a residual debug test.
- **Documentation**: "64 commands" counters → 65 (SPEC_CLI,
  CHECKLIST_SPEC_WEB, agent skills); corrected path in TESTING.md.
- **`agent status` finishing touch**: missing `completion_rate`/`qualitative`
  displayed as "—" (not "None"); **escalation threshold constraint
  documented in SPEC.txt F9 and SPEC_CLI §4.9** (integers >= 1, `null`/`0`
  refused — immediate escalation otherwise).
- **Test port isolation**: `SYNAPSE_WEB_PORT` / `SYNAPSE_A2A_PORT` (random
  free ports set by the test helpers) — the full suite now passes on a
  machine where production already listens on 8080/8090 (conflict found at
  deployment: the production web occupied the test web's port 8080);
  `update apply` resolves the restarted web port via the same variable.
- **Version 3.1.1**: bump `pyproject.toml` (RELEASE.md step 2, missed by the
  audit) + updated version assertions.

### Added

- **Production audit**: `docs/PRODUCTION_AUDIT.md` (master plan, 13 tracked
  issues) and `docs/PRODUCTION_PROGRESS.md` (checklist with proofs).

## [3.1.0] — 2026-08-07

### Added

- **Full production deployment** (`docs/SPEC_PRODUCTION.md`, 8 points):
  - **systemd supervision of the 3 services**: `synapse.service` (hardened),
    `synapse-web.service` and the `synapse-a2a@.service` template (one
    instance per exposed agent, 0600 secrets read by the wrapper and passed
    via stdin — never as arguments or environment).
  - **Automated backup**: daily timer (02:00, `Persistent=true`), retention
    `synapse backup prune --keep 14`, **weekly restore proof**
    `synapse backup verify` (isolated storage, production untouched),
    `backup.key.vault` backup copy verified by the monitor.
  - **Passive supervision**: minimal `sd_notify` client
    (`synapse/systemd_notify.py`, zero dependency, inert outside systemd),
    heartbeats on all 6 daemon paths, `WatchdogSec=30` (freeze detection),
    periodic monitor (`scripts/synapse-monitor.py`, every 5 min: services,
    backup, database, disk, errors, key; `monitor.json` + `alert_command`).
  - **Local CI**: `scripts/ci.sh` (dedicated venv, lock check, full suite),
    blocking pre-push hook (`scripts/install-git-hooks.sh`), nightly timer.
  - **Release cycle**: `synapse --version` / `synapse version`, removal of
    `__version__` (single source `importlib.metadata`), `CHANGELOG.md`,
    `docs/RELEASE.md`, version 3.1.0.
  - **systemd hardening**: memory bounds (`MemoryHigh=4G`/`MemoryMax=6G`
    server, `512M` web/a2a), `OOMScoreAdjust=500`,
    `RestrictAddressFamilies`, `CapabilityBoundingSet=`,
    `SystemCallFilter=@system-service`, `StateDirectory`/`LogsDirectory`/
    `RuntimeDirectory`.
  - **Dependency pinning**: `requirements.lock` generated with pip-compile
    hashes, installed by `install.sh` with `--require-hashes` + `--no-deps`.
  - **Documentation**: README portal updated (65 commands, unified CLI,
    F18/F20), OPERATIONS production runbook.
- **systemd-driven `update apply`**: unit detection (`systemctl`), A2A bridge
  included in the plan (stop/restart of `synapse-a2a@*.service` instances);
  legacy CLI behavior kept outside systemd.
- **`synapse status`**: A2A bridge state added (stopped = legitimate since
  optional; degraded = anomaly).

### Changed

- `pyproject.toml`: version 3.1.0; `pip-tools` added to the dev extras.
- `synapse/cli/update.py`: 8-step update plan (web → A2A → server → command
  → restarts).
- `synapse/backup.py`: factored archive reading (`_decrypt_archive`), `prune`
  and `verify` added.
- `synapse/cli/backup.py`: `prune` and `verify` subcommands (with `--latest`
  and `--dir`).
- `config.example.json`: `alert_command` and `update_command` documented.
- `install.sh`: rewritten (units from `scripts/systemd/`, dependency lock,
  secrets, `backup.key.vault`).

### Removed

- `__version__` from `synapse/__init__.py` (single version source:
  `importlib.metadata` — `project_version()`).

## [3.0.0] — earlier

Previous versions: unified CLI overhaul, web UI v3 "Registre", A2A bridge,
65-command API v2, production validation benches (A2A interop, update cycle).
Detailed history in the repository's commit messages.
