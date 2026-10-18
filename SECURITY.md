# Security policy

## Reporting a vulnerability

Please report vulnerabilities **privately**, through
[GitHub's private vulnerability reporting](https://github.com/avanserv/odouche/security/advisories/new).
Do not open a public issue for one.

Say what you found, how to reproduce it, and what it lets someone do. You will get an
acknowledgement, and the fix and its disclosure are coordinated with you through the advisory.

If what you found is a weakness in Odoo.sh itself rather than in this project, report it to Odoo
through their [responsible disclosure](https://www.odoo.com/security-report) process instead.

## Supported versions

The project is pre-1.0: only the latest release receives fixes.

## What this project guarantees

odouche acts on Odoo.sh projects with its user's access. The constraints it is held to are in
[docs/security.md](docs/security.md); a behaviour that breaks one of them is a vulnerability and is
in scope for a report:

- A password or a GitHub token being seen, stored or logged.
- A session value written to a plain-text file, or appearing in logs, exceptions, command output or
  MCP tool results.
- A session used past the point Odoo.sh invalidated it.
- An MCP tool changing state on Odoo.sh without having been opted into.
