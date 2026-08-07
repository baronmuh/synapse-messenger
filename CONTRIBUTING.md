# Contributing to Synapse

Thanks for considering contributing to Synapse! This project is built for
organizations of AI agents — and it is built by humans (and agents) working
together. This guide explains how to contribute cleanly.

## Table of contents

- [Code of conduct](#code-of-conduct)
- [Licensing and the Contributor License Agreement](#licensing-and-the-contributor-license-agreement)
- [Development setup](#development-setup)
- [Workflow](#workflow)
- [Commit guidelines](#commit-guidelines)
- [Release policy](#release-policy)

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be
respectful, constructive, and assume good faith.

## Licensing and the Contributor License Agreement

Synapse is distributed under **dual licensing**:

1. **AGPL-3.0** — the open source license, for everyone (see `LICENSE`);
2. **Commercial license** — a paid license for companies that want to
   integrate Synapse into a closed product or service without the AGPL
   obligations (contact the maintainers for terms).

To keep both licenses viable, **every contribution must be licensed under
both**. By submitting a pull request, you agree to the following
**Contributor License Agreement (CLA)**:

> I grant the Synapse maintainers a perpetual, worldwide, non-exclusive,
> royalty-free, irrevocable license to use, modify, distribute and
> sublicense my contribution under **both** the AGPL-3.0 license and the
> project's commercial license, and to relicense it as part of the project
> as the project's licensing evolves. I confirm that I have the right to
> grant this license (my contribution is my own work, or I have permission
> from its owner).

By opening a pull request you are deemed to have accepted this CLA. If you
contribute on behalf of a company, ensure the company agrees to these terms
before submitting.

## Development setup

Requirements: Linux, Python >= 3.11, a virtual environment.

```bash
git clone https://github.com/baronmuh/synapse-messenger.git
cd synapse-messenger
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

The `dev` extra installs the tooling used by the project: pytest, pytest-xdist,
pytest-cov and pip-tools.

## Workflow

1. **Open an issue** first for any non-trivial change, so the design is
   discussed before code is written.
2. **Create a branch** from `main` (`git checkout -b feat/your-change`).
3. **Implement**, following the project's conventions:
   - the server API is versioned (65 commands) — never change the wire
     contract without a major version bump and an explicit design discussion;
   - secrets never appear on command lines or in environment variables —
     they are read from files or stdin only;
   - all user-facing text is in English.
4. **Run the test suite** (see below) and make sure it is green.
5. **Open a pull request** describing the change and referencing the issue.

### Running the tests

The full suite is fast and parallel-safe (each test uses isolated ports and
temporary directories):

```bash
.venv/bin/pytest tests/ -q -n 3
```

If `pytest-xdist` is unavailable, run it sequentially:

```bash
.venv/bin/pytest tests/ -q
```

Do not weaken tests to make them pass — fix the code.

## Commit guidelines

- One logical change per commit.
- Imperative, concise subject line; explanatory body when needed.
- Conventional prefixes are welcome: `feat:`, `fix:`, `docs:`, `test:`,
  `refactor:`, `perf:`, `ci:`, `chore:`.

## Release policy

- Semantic Versioning (SemVer): `MAJOR.MINOR.PATCH`.
- Breaking API changes require a major version bump.
- Releases are tagged `vX.Y.Z` and published as GitHub Releases, with notes
  summarized in `CHANGELOG.md`.

## Questions?

Open an issue with the `question` label, or reach out via the discussion
area of the repository.
