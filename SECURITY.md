# Security Policy

Synapse is an infrastructure component for organizations of AI agents. It
handles credentials, messages and task data — security is treated as a
first-class concern.

## Supported versions

| Version | Supported |
| ------- | --------- |
| 3.x     | ✅         |
| < 3.0   | ❌         |

## Reporting a vulnerability

Please **do not open a public issue** for security vulnerabilities. Report
them privately via GitHub's **Security Advisories**:

1. Go to the repository's **Security** tab;
2. Click **Report a vulnerability**;
3. Describe the vulnerability, the impact, and a minimal reproduction.

You can also email the maintainers directly (see the repository profile for
the contact address).

We aim to acknowledge reports within **48 hours** and to provide a first
assessment within **7 days**. We keep reporters informed until a fix is
released. If the vulnerability is accepted, a fix is prepared privately and
released with an advisory; reporters are credited unless they prefer to stay
anonymous.

## Security principles of the project

- Credentials are never stored in plain sight: initial passwords are kept in
  root-only files, secrets are passed via stdin, never via command-line
  arguments or environment variables.
- The server listens on a Unix socket with restrictive permissions and on the
  loopback interface only — no public network exposure by design.
- All writes go through SQLite transactions; backups are encrypted and
  verifiable (restore proof).
- Every command is authenticated and authorized individually; a standard
  account cannot escalate privileges, and organizations are permanent
  (no deletion path).
- Systemd units enforce memory limits, filesystem restrictions and explicit
  directory permissions.

If you find a way around any of these, we want to hear about it — privately.
