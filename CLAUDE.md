# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`odouche` helps Odoo developers work with Odoo.sh from their dev environment. Odoo.sh has no
official public API; this project is an **unofficial** client for what the Odoo.sh web UI uses.

Status: the workspace, tooling and CI exist. The CLI has the commands of its first milestone:
`osh auth`, `osh projects list`, `osh branches list`, `osh builds list`, `show`, `watch` and
`rebuild`, `osh logs`, `osh ssh` and `osh firewall list` and `allow`, with a reference page
generated from them.
The MCP server has eight read-only
tools: `get_session`, `list_projects`, `list_branches`, `list_builds`, `get_build`, `wait_for_build`,
`read_log` and `get_firewall`. Two more, `rebuild_branch` and `update_firewall`, change state and are
registered only with `--allow-changes`. A reference page is generated from the tools. The library has its transport, session
store, login, logout and a client that
reports the session's user, lists projects, branches and builds, watches a build, reads and
follows a build's logs, triggers a rebuild, gives the SSH target of a build, and reads and changes
a project's firewall. The architecture
and security sections below are
the design the implementation is held to.

## Architecture

A uv workspace with three distributions under `packages/`, layered strictly one way:

| Package | Role |
| --- | --- |
| `odouche` | Python library: the API proxy for Odoo.sh (projects, branches, builds, logs). Usable on its own in third-party tools. |
| `odouche-cli` | The `osh` command, in the spirit of `gcloud` / `aws` / `scw`. |
| `odouche-mcp` | MCP server exposing the same capabilities to development agents. |

Rules that hold across packages:

- `odouche-cli` and `odouche-mcp` depend on `odouche` and only on its public API. They never talk
  to Odoo.sh directly and never import each other. `make deps` enforces this: deptry checks each
  package against its own `pyproject.toml`, so an import the package does not declare fails.
- A dependency is declared in the same change as the code that imports it, never ahead of it
  (deptry also fails on a declared dependency nothing imports).
- All knowledge of Odoo.sh endpoints, payload shapes and scraping lives in one layer inside
  `odouche`. Upstream is undocumented and can change without notice; a breakage should be fixable
  in that layer without touching the CLI or the MCP server.
- The library returns typed models, not raw upstream payloads. Rendering (tables, JSON, MCP tool
  results) is the frontends' job.
- A capability is added to the library first, then surfaced in the CLI and/or the MCP server.
- Long-running operations (watching builds, streaming logs) are exposed by the library as
  iterators/streams so each frontend can present them its own way.

### Library shape

- The public API is synchronous and written once; there is no async surface. A CLI command calls
  it directly. The MCP server runs each tool in an anyio worker thread, so the event loop never
  blocks.
- A call running in a thread cannot be interrupted, so every call is bounded: each request has a
  timeout and each stream takes a deadline.
- Watching a build and following a log return a generator of typed events: the build's changed
  state, or a line of log text with its offset. A watch ends on a terminal state. Closing the
  generator closes the connection. A passed deadline raises a typed timeout error, never a silent
  end.
- A generator is closed only between two events. A watch takes a pulse, at which it yields the
  build unchanged, so a caller in a thread can stop it: the MCP wait checks for a cancelled call
  there.
- Build status comes from upstream's bus websocket, opened by the transport so the host pin covers
  it. The socket gives no sign of a missing or expired session, so a watch also asks `builds`: at
  the start, after a reconnect and after a quiet spell. That request is what detects an
  unauthenticated session and what catches an event the socket missed.
- Logs are polled with `Range` requests, as the page does. They are on the build's worker, which
  the transport accepts only as `https://<label>.odoo.com` and asks with the project's access
  token, never with the session. The token is kept in memory for the one call.
- The HTTP client is `httpx2`, with its `ws` extra for the socket. Redirects are off unless a
  request asks for them, responses stream, and `MockTransport` serves the request tests with no
  network and no socket patching.
- Models are frozen standard dataclasses. One reader in the upstream layer checks each field and
  raises the "upstream changed shape" error naming the request and the field.
- Rejected: an async surface with the sync one derived from it (a background event loop for every
  sync caller), both surfaces over a sans-IO core (every polling loop becomes a state machine),
  `httpx` (no release since 2024-12), `urllib3` and `requests` (no mock transport, no websocket),
  `aiohttp` (async only), pydantic (a compiled dependency in a library others embed, and its base
  class becomes public API), msgspec (upstream's `false` and `[id, name]` pairs need the same
  hand-written hooks), polling alone for build status (upstream sets no interval: the page never
  polls).

## Security model

These are design constraints, not guidelines:

- The user's credentials (password, GitHub token) are never seen, stored, or logged.
- Authentication is a browser-based GitHub login flow. Only the resulting Odoo.sh session is kept
  across runs, in the OS keyring — never in a plaintext file, config file, or cache.
- The store accepts three keyring backends and picks among them itself, not through
  `keyring.get_keyring()`: Secret Service (Linux, and WSL2 once a provider is installed), macOS
  Keychain, Windows Credential Locker. Any other backend (`fail`, `null`, a chainer pick,
  `keyrings.alt`, libsecret, KWallet, any third party) is an error with a message, not a fallback.
- With no accepted backend, as on a bare WSL2, in a container or in CI, nothing is persisted. The
  error names the two ways out: install a Secret Service provider, or supply the session through
  the environment. The session is never written to a file or printed for the shell to export.
- Every keyring call is bounded: it runs in a daemon thread that is left behind at the bound, and
  a write happens only after a read has shown the keyring unlocked and the wait has not been given
  up. The thread and the dialog outlive the call, and the next call waits for that thread rather
  than starting another. Not covered: on macOS Keychain a write can prompt on its own, so one
  given up on there can still land, and its error says so. Credential Locker shows no dialog.
- Upstream expiry is authoritative: an unauthenticated response invalidates the stored session and
  requires a new login. Sessions are never refreshed or extended. A client-side max age applies
  on top of upstream expiry.
- Headless use takes the session from the environment and keeps it in memory only.
- Session values must not appear in logs, exceptions, `repr()`s, CLI output, or MCP tool results.
- Every `osh` command opens its client read-only, except `osh builds rebuild` and
  `osh firewall allow`, which ask before they send unless `--yes` is given.
- `osh firewall allow` with no address asks https://api.ipify.org for the public one, with the
  standard library: the one request odouche sends outside Odoo.sh, its workers and the login. The
  library itself never looks the address up, and neither does the MCP server: `update_firewall`
  takes the address from the agent, and adds it unless asked to replace the others.
- `osh ssh` replaces its process with the user's own `ssh`. odouche never reads, generates or
  uploads an SSH key, and the target from upstream is checked as a user and a host before it is
  an argument. There is no MCP tool for it, opt-in or not.
- The MCP server is read-only by default; tools that change state on Odoo.sh are opt-in and
  documented as such. Every tool documents what it does and what it can touch.

Session capture, at the end of the browser flow:

- odouche launches a Chromium-family browser found on the system, with a fresh profile that is
  deleted when the login ends, and reads the cookie over `--remote-debugging-pipe`. It needs no
  Python dependency and never downloads a browser.
- The protocol could observe the GitHub page, so "never seen" rests on the capture code being
  short and reviewable, not on it being impossible. That code sends only `Target.getTargets`
  once at launch, `Target.attachToTarget`, `Network.getCookies` restricted to
  `https://www.odoo.sh`, and `Browser.close`. It enables no domain, subscribes to no event and
  injects no script.
- With no display or no Chromium-family browser, the user pastes the cookie into a hidden prompt.
  Native Windows pastes too: the browser is not launched there yet.
- A captured or pasted session is stored only once the cookie is known to belong to the host the
  upstream reference names and it has answered one authenticated request.
- Rejected: the everyday browser's cookie store (it holds every other site's session), a browser
  extension (a standing install per browser, with cookie access), a persistent profile (a GitHub
  session on disk outside the keyring), a debugging port (open to any local process), Playwright
  (downloads browsers, too large to review).

## Commands

Toolchain: uv (never `pip`), ruff, basedpyright, deptry, vulture, pytest, zensical, prek.
Python >= 3.12; `.python-version` pins 3.12 locally so the floor is what gets exercised, and CI
tests 3.12 to 3.14.

```bash
make install        # uv sync + install the git hooks
make check          # full gate, non-mutating: what CI runs
make format         # ruff format
make lint           # ruff check with fixes, zizmor on the workflows
make type-check     # basedpyright
make deps           # deptry, once per package
make test           # pytest
make coverage       # pytest with coverage; fail_under = 90
make live-check PROJECT=<name>   # read path against the real Odoo.sh; by hand, never in CI
make docs           # zensical build --strict, into site/
make docs-cli       # write docs/cli-reference.md from the commands of osh
make docs-mcp       # write docs/mcp-reference.md from the tools of odouche-mcp
make build          # wheel + sdist of each package, into dist/
make hooks          # every prek hook over every tracked file

uv run pytest packages/odouche                               # one package
uv run pytest packages/odouche/tests/test_x.py::test_name    # one test
uv run osh --help                                            # run the CLI from the workspace
uv add --package odouche-cli <dep>                           # add a dependency to one package
uv add --dev <dep>                                           # add a development tool (root)
```

Tool configuration lives in the root `pyproject.toml`, except basedpyright (`pyrightconfig.json`)
and each package's own deptry exceptions. The hooks in `.pre-commit-config.yaml` run the Python
tools through `uv run`, so they use the versions `uv.lock` pins.

## Commits and releases

- Conventional Commits; pull requests are squash-merged, so the PR title is the commit and CI
  checks it.
- Never edit a version. Release Please owns them: the three packages and the workspace root are
  versioned in lockstep, and the `# x-release-please-version` markers in the package manifests are
  what it rewrites (including the `odouche==X` pin in the two frontends), with the
  `odouche-mcp==X` of the setup snippets in `docs/mcp.md` and the MCP README.
- Merging the release PR tags the release and publishes to PyPI by trusted publishing
  (`.github/workflows/release.yml`). CONTRIBUTING.md has what to wait for before merging one.

## Testing

Tests never contact the real Odoo.sh. Upstream responses are mocked or replayed from fixtures,
and fixtures must be scrubbed of session values and real project data before being committed.
