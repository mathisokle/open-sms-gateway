# Security policy

## Reporting a vulnerability

Please report security issues **privately**, not as a public GitHub issue.

Use GitHub's [private vulnerability reporting](https://github.com/mathisokle/open-sms-gateway/security/advisories/new)
(Security → Report a vulnerability). If that is unavailable to you, open a regular issue titled
"Security contact request" containing no details, and a private channel will be arranged.

Please include, as far as you can:

- what an attacker can achieve, and what access they need to start
- affected version or commit
- reproduction steps or a proof of concept
- whether the issue is already public anywhere

This is a spare-time project with a single maintainer. Expect an acknowledgement within about a
week. Fixes are released as a normal commit on `main` with the issue described in the notes; there
is no separate patch branch.

## Scope

This repository is the gateway software. **Out of scope** are the operating system, Docker, the
modem's firmware, `python-gammu`, and the security of any deployment that ignores the documented
constraints — in particular exposing the admin panel directly to the internet.

## Known and accepted limitations

Please read [docs/security.md](docs/security.md) before reporting. The following are documented
design trade-offs for a single-operator tool on a small self-hosted box, not undisclosed bugs:

- No CSRF tokens; the panel relies on the `SameSite=Lax` session cookie
- No TLS of its own; TLS is expected from a reverse proxy
- Containers run as root, because the worker needs the mapped serial device
- The SQLite database and its backups are unencrypted
- `RATE_LIMIT_PER_MINUTE` is one shared in-memory window, not a per-client quota

A report showing that one of these is *worse than documented* — for example a `SameSite` bypass, or
a way to reach the panel without a session — is very welcome.

## Handling of secrets

The codebase treats these as never-loggable: plaintext API tokens, `webhook_secret`,
`ADMIN_PASSWORD`, `SECRET_KEY`, and SMS message bodies. **A code path that writes any of them to a
log, an error message or a traceback is a vulnerability**, even without another bug attached, and is
in scope.
