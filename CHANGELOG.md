# Changelog

All notable changes to the Synapse project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project follows [SemVer](https://semver.org/).

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
