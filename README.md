# Odoo.sh Toolkit — `osh`, an MCP server and a Python library for Odoo.sh

[![CI](https://github.com/abdallah-abdelsabour/odoo-sh-toolkit/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/abdallah-abdelsabour/odoo-sh-toolkit/actions/workflows/ci.yml?query=branch%3Amain)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)

Work with your [Odoo.sh](https://www.odoo.sh) projects from the terminal, from scripts and CI,
and from AI coding agents: list projects and branches, watch builds, **stream build and server
logs live**, rebuild a branch, open an SSH shell on a build and manage the project's firewall,
without opening the Odoo.sh web page.

```bash
git push && osh builds watch || osh logs        # push, wait for the build, read why it failed
osh logs --project acme --branch production --kind odoo -f   # tail the production server log live
```

> [!IMPORTANT]
> **Unofficial.** This project is not affiliated with, endorsed by, or supported by Odoo S.A.
> Odoo.sh has no public API: this toolkit talks to the same endpoints the Odoo.sh web interface
> uses, which can change without notice. When they do, the tools say so with a clear error
> (exit code 6) instead of returning wrong data.

---

## Table of contents

- [Why this toolkit](#why-this-toolkit)
- [Why it is a good fit for AI agents working on Odoo.sh](#why-it-is-a-good-fit-for-ai-agents-working-on-odoosh)
- [What is inside](#what-is-inside)
- [Installation](#installation)
- [Quick start (5 minutes)](#quick-start-5-minutes)
- [Core concepts](#core-concepts)
- [Command reference](#command-reference)
  - [Global options](#global-options)
  - [`osh auth`](#osh-auth) — log in, log out, session status
  - [`osh projects`](#osh-projects) — list projects
  - [`osh branches`](#osh-branches) — list branches
  - [`osh builds`](#osh-builds) — list, show, watch, rebuild
  - [`osh logs`](#osh-logs) — read and follow logs
  - [`osh ssh`](#osh-ssh) — shell on a build
  - [`osh firewall`](#osh-firewall) — list and change allowed IPs
- [Tutorials](#tutorials)
- [Using it with AI agents (MCP server)](#using-it-with-ai-agents-mcp-server)
- [Using it from Python (library)](#using-it-from-python-library)
- [Output formats and scripting](#output-formats-and-scripting)
- [Exit codes](#exit-codes)
- [Environment variables](#environment-variables)
- [Security model](#security-model)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Credits and original project](#credits-and-original-project)
- [License](#license)

---

## Why this toolkit

Odoo.sh only has a web interface. The usual loop for an Odoo developer is:

1. `git push`
2. Open the browser, find the project, find the branch, wait for the build to turn green or red.
3. Click into the build, open the right log tab, scroll to find the traceback.
4. Repeat for every push, on every branch, for every client project.

With `osh` that loop becomes one line in your terminal, and the same information is available to
scripts, CI pipelines and AI agents:

| Task | Web UI | With `osh` |
| --- | --- | --- |
| Is my build done? | Refresh the branch page | `osh builds watch` (exits with the result) |
| Why did it fail? | Open build → Logs → install tab → scroll | `osh logs` |
| Tail production's server log | Logs tab, keep it open | `osh logs --branch production --kind odoo -f` |
| Find errors in the log | Ctrl+F in the browser | `osh logs --kind odoo --all \| grep -E "ERROR\|CRITICAL"` |
| Rebuild staging | Branch menu → Rebuild → confirm | `osh builds rebuild --branch staging --watch` |
| Shell on the build | Copy the SSH command from the page | `osh ssh` |
| Let my new IP in by SSH | Settings → Firewall → edit | `osh firewall allow --add` |
| Use from CI / a script | Not possible | `--format json`, stable exit codes |

The project and branch are **guessed from your git checkout**: inside a repository that an Odoo.sh
project builds, `osh builds watch` and `osh logs` need no arguments at all.

## Why it is a good fit for AI agents working on Odoo.sh

AI coding agents (Claude Code, Cursor, Codex, Copilot, Windsurf and others) are good at writing
Odoo modules, but on Odoo.sh they are blind: they push code and can't see whether the build
passed or what the traceback was. You end up copying logs from the browser into the chat.

This toolkit gives the agent its own eyes on Odoo.sh, in two ways:

1. **An MCP server (`odouche-mcp`)** with typed tools (`list_projects`, `list_branches`,
   `list_builds`, `get_build`, `wait_for_build`, `read_log`, `get_firewall`, and optionally
   `rebuild_branch` and `update_firewall`). Any MCP client can call them directly.
2. **The `osh` CLI**, which an agent with a shell can run, with `--format json` output and
   documented exit codes that are easy for a model to read.

What that unlocks:

- **Closed feedback loop.** The agent writes code → pushes → calls `wait_for_build` with the
  commit hash → if the build failed, calls `read_log` (filtered with `contains: "Traceback"`) →
  fixes the code → pushes again. No copy-paste from you.
- **Production diagnosis.** "Why is the invoice report slow on production?" The agent reads the
  `odoo` log, finds the slow requests, then reads the code that serves them.
- **Safe by default.** The MCP server is **read-only** unless *you* start it with
  `--allow-changes`. No tool an agent can call turns that on. Production is never rebuilt by any
  tool, even with the flag.
- **Prompt-injection aware.** Log lines are returned in a field named `untrusted_lines`, and the
  tool description tells the agent to treat them as data, never as instructions.
- **Your session never reaches the agent.** The server never returns, logs or accepts the Odoo.sh
  session as an argument. The agent cannot log in on its own: you log in once with `osh auth login`.
- **Bounded calls.** Every tool has limits on size and time (for example `read_log` returns at
  most 500 lines / 64 KiB, `wait_for_build` waits at most 50 s per call and tells the agent to call
  again), so an agent never hangs or floods its context.

See [Using it with AI agents](#using-it-with-ai-agents-mcp-server) for setup and example prompts.

## What is inside

A [uv](https://docs.astral.sh/uv/) workspace with three Python packages, in `packages/`:

| Package | Command | What it is |
| --- | --- | --- |
| [`odouche`](packages/odouche) | — | The Python library: the only layer that talks to Odoo.sh. Returns typed models. Usable in your own tools. |
| [`odouche-cli`](packages/odouche-cli) | `osh` | The command-line tool, in the spirit of `gcloud`, `aws` and `scw`. |
| [`odouche-mcp`](packages/odouche-mcp) | `odouche-mcp` | A Model Context Protocol server for AI agents. |

The CLI and the MCP server are built only on the library's public interface, so an Odoo.sh change
is fixed in one place.

Detailed guides live in [`docs/`](docs): [CLI guide](docs/cli.md),
[CLI reference](docs/cli-reference.md), [MCP guide](docs/mcp.md),
[MCP reference](docs/mcp-reference.md), [library guide](docs/library.md),
[security](docs/security.md) and [how the upstream endpoints work](docs/upstream.md).

## Installation

**Requirements:** Python 3.12+ (uv installs it for you), [uv](https://docs.astral.sh/uv/), `git`.
For `osh auth login` with a browser: a Chromium-family browser (Chrome, Chromium, Edge, Brave).
For `osh ssh`: an `ssh` client (Linux, macOS, WSL).

Install uv if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Option A — install `osh` from this repository (recommended)

```bash
uv tool install "git+https://github.com/abdallah-abdelsabour/odoo-sh-toolkit#subdirectory=packages/odouche-cli"
osh --version
```

Upgrade later with `uv tool upgrade odouche-cli`, remove with `uv tool uninstall odouche-cli`.

### Option B — clone and install in editable mode (to hack on it)

```bash
git clone https://github.com/abdallah-abdelsabour/odoo-sh-toolkit.git
cd odoo-sh-toolkit
uv tool install --editable ./packages/odouche-cli --with-editable ./packages/odouche
```

`--editable` links to the folder instead of copying it: a change to a `.py` file is used by the
next `osh` run, with nothing to reinstall. Don't move or delete the folder afterwards.

### Option C — run once without installing

```bash
uvx --from "git+https://github.com/abdallah-abdelsabour/odoo-sh-toolkit#subdirectory=packages/odouche-cli" osh --help
```

### The MCP server

The MCP server is run by your AI client, not installed by hand. See
[Using it with AI agents](#using-it-with-ai-agents-mcp-server).

### Shell completion (optional)

```bash
osh --install-completion     # bash, zsh, fish, PowerShell; takes effect in a new shell
```

Commands, options and option values complete. Project and branch names do not (completion never
calls Odoo.sh).

### WSL2 note

On WSL2 the session is stored in a Secret Service keyring, which a fresh Ubuntu does not run.
Install one once, for example:

```bash
sudo apt install gnome-keyring
```

Or skip the keyring and pass the session through the environment: see
[`ODOUCHE_SESSION`](#environment-variables).

## Quick start (5 minutes)

```bash
# 1. Log in once (opens a browser, sign in with GitHub as on odoo.sh). Lasts up to 30 days.
osh auth login

# 2. See what you can reach
osh projects list
osh branches list --project acme

# 3. Look at a branch's builds and logs
osh builds list --project acme --branch staging
osh logs --project acme --branch staging

# 4. From inside a checkout of the repo that the project builds, no arguments needed
cd ~/code/acme
git push && osh builds watch
osh logs
```

Sample output:

```text
$ osh branches list --project acme
  Name            Stage
  production      production
  staging         staging
* feature-x       development
  fix-invoice     development

$ osh builds watch
Build 1234 succeeded: https://acme-feature-x-1234.dev.odoo.com
```

## Core concepts

### Project and branch: how `osh` finds them

Every command that works on a project or a branch takes the first value it finds, in this order:

| Order | Project | Branch |
| --- | --- | --- |
| 1 | `--project NAME` | `--branch NAME` |
| 2 | `OSH_PROJECT` environment variable | `OSH_BRANCH` environment variable |
| 3 | The git checkout's GitHub remote (upstream of the current branch, else `origin`) | The git checkout's current branch |

- The project is given **by its name**, as `osh projects list` shows it (the last part of
  `https://www.odoo.sh/project/<name>`), not by the repository name.
- If several of your projects build the same repository, `osh` never guesses: it exits 2 and lists
  them, so you add `--project`.
- A repository none of your projects build exits 4. A detached HEAD gives no branch.
- `osh --debug <command>` prints on stderr which source was used.

Tip: set them once for a terminal session:

```bash
export OSH_PROJECT=acme OSH_BRANCH=staging
osh builds list && osh logs
```

### Stages

Each branch is in one stage: `production`, `staging` or `development` (`unknown` for anything
`osh` does not recognise). Only **development** and **staging** branches can be rebuilt; `osh`
refuses production before sending anything.

### Builds

A build is what Odoo.sh made of a commit on a branch. It has a number (`BUILD_ID`), a **status**
(`progress`, `updating`, `done`, `dropped`, `skipped`, `killed`) and, once finished, a **result**
(`success`, `failed`, `warning`). Odoo.sh only exposes the **latest ~4 builds** of a branch:
older ones are out of reach.

### Log kinds

A build has several log files. `osh logs --kinds` lists the ones it has:

| Kind | What it holds | Typical branch |
| --- | --- | --- |
| `install` | Module installation and tests of a fresh build | development |
| `odoo` | The running server's log (requests, errors, crons) | every running build, most useful on production/staging |
| `update` | Module updates when a new commit is deployed | production, staging |
| `pip` | Installing `requirements.txt` | any |
| `neutralize` | Neutralizing a copied database | staging, development |
| `upgrade` | Version upgrade logs | upgrade builds |
| `unknown` | Any other file Odoo.sh serves, e.g. `pg_slow_queries`, picked by its name | — |

Without `--kind`, `osh logs` reads `install` when the build has one, `odoo` otherwise.

## Command reference

Every command also takes `--help`. The full generated reference is in
[docs/cli-reference.md](docs/cli-reference.md).

### Global options

Global options go **before** the command: `osh --format json builds list`.

| Option | Description | Env var |
| --- | --- | --- |
| `--version` | Show the versions of `osh` and the library, and exit. | |
| `--debug` | Show the traceback when a command fails, and which source gave the project/branch. | `OSH_DEBUG=1` |
| `--format FORMAT` | `table` (default) or `json`. Only the result goes to stdout, so `json` pipes cleanly into `jq`. | |
| `--install-completion` | Install shell completion for the current shell. | |
| `--show-completion` | Print the completion script instead of installing it. | |

### `osh auth`

Log in, log out, and see the session in use.

#### `osh auth login`

Sign in to Odoo.sh with GitHub in a browser window and store the session in your OS keyring.

```bash
osh auth login
```

- A **temporary** browser profile is used and deleted afterwards; `osh` reads only the
  `www.odoo.sh` cookies. Your password and GitHub token are never seen.
- Where no browser can be launched (SSH, container, Windows, Linux with no `DISPLAY`), it asks for
  the `session_id` cookie in a prompt that doesn't echo. Get it from your own browser: log in to
  odoo.sh → DevTools → Application → Cookies → `https://www.odoo.sh` → `session_id`.
- Exits 8 if nobody completes the login in time, 11 if there is no usable keyring, 12 if the login
  ended without a session.

#### `osh auth status`

Show where the session comes from (keyring or environment), when it was stored and when it expires,
**without** calling Odoo.sh. Exits 3 when there is none, so scripts can test for it.

| Option | Description |
| --- | --- |
| `--check` | Also ask Odoo.sh whether it still accepts the session, and show the user it belongs to. |

```bash
osh auth status
osh auth status --check
osh auth status >/dev/null || osh auth login     # log in only when needed
```

#### `osh auth logout`

End the stored session on Odoo.sh and delete it from the keyring. Exits 0 even when there was none.
A session from `ODOUCHE_SESSION` is left alone: unset the variable yourself.

```bash
osh auth logout
```

### `osh projects`

#### `osh projects list`

List the projects you can reach, with their GitHub repository and Odoo.sh address. No options.

```bash
osh projects list
osh --format json projects list | jq -r '.[].name'
```

### `osh branches`

#### `osh branches list`

List a project's branches with their stage: production, staging, then development, each sorted by
name. `*` marks the branch you have checked out (when the project came from the checkout).

| Option | Description | Env var |
| --- | --- | --- |
| `--project PROJECT` | The project's name. Default: the git checkout's. | `OSH_PROJECT` |
| `--stage STAGE` | Only branches in this stage: `production`, `staging`, `development`, `unknown`. Repeatable. | |

```bash
osh branches list --project acme
osh branches list --project acme --stage staging --stage production
```

### `osh builds`

List, show and watch a branch's builds, and start a new one.

#### `osh builds list`

The branch's latest builds, newest first, with status, result and commit.

| Option | Description | Env var |
| --- | --- | --- |
| `--project PROJECT` | The project's name. Default: the git checkout's. | `OSH_PROJECT` |
| `--branch BRANCH` | The branch's name. Default: the git checkout's. | `OSH_BRANCH` |
| `--limit LIMIT` | The most builds to list (≥ 1). Default `4`; Odoo.sh has only been seen to answer 4. | |

```bash
osh builds list --project acme --branch staging
osh --format json builds list | jq -r '.[] | "\(.id) \(.status) \(.result) \(.commit.message)"'
```

#### `osh builds show [BUILD_ID]`

One build with its commit (hash, message, author, time) and the URL of its database. Default: the
latest build.

| Argument / option | Description | Env var |
| --- | --- | --- |
| `BUILD_ID` | The build's number, among the branch's latest builds. Default: the latest. | |
| `--project PROJECT` | The project's name. Default: the git checkout's. | `OSH_PROJECT` |
| `--branch BRANCH` | The branch's name. Default: the git checkout's. | `OSH_BRANCH` |

```bash
osh builds show
osh builds show 1234 --project acme --branch feature-x
osh --format json builds show | jq -r .url          # open the build's database
```

`list` and `show` always exit 0, whatever the build's result.

#### `osh builds watch [BUILD_ID]`

Follow a build until it finishes and **exit with its result** (0 success, 20 failed, 21 warning,
22 no result, 23 timeout). By default it waits for the build **of the commit you just pushed**.

| Argument / option | Description | Env var |
| --- | --- | --- |
| `BUILD_ID` | Watch this build. Default: the build of HEAD when project and branch are the checkout's, the latest build otherwise. | |
| `--project PROJECT` | The project's name. Default: the git checkout's. | `OSH_PROJECT` |
| `--branch BRANCH` | The branch's name. Default: the git checkout's. | `OSH_BRANCH` |
| `--timeout SECONDS` | Longest wait for the build to appear and finish. Default `1800` (30 min). | |
| `--commit SHA` | Wait for a build of this commit (7–64 hex digits). Not with `BUILD_ID` or `--no-wait`. | |
| `--no-wait` | Watch the branch's latest build, whatever its commit. | |

How it behaves:

- In your checkout, it waits for a build of HEAD to appear (asking every 3 s, up to 2 minutes, then
  exits 23: did you push?), then follows it.
- Progress goes to **stderr**: a single updating line on a terminal, one line per change otherwise.
  The final line goes to **stdout**: `Build 1234 failed: https://acme-feature-x-1234.dev.odoo.com`.
- Ctrl+C exits 130 and leaves the build running — watching never changes anything.

```bash
git push && osh builds watch
osh builds watch --project acme --branch feature-x --no-wait
osh builds watch --commit 3f2a9c1 --timeout 3600
git push && (osh builds watch || osh logs)          # show the log only when it didn't succeed
```

#### `osh builds rebuild`

**Changes state on Odoo.sh:** starts a new build of a branch, which replaces its latest one. Only
development and staging branches; production exits 9 before anything is sent.

| Option | Description | Env var |
| --- | --- | --- |
| `--project PROJECT` | The project's name. Default: the git checkout's. | `OSH_PROJECT` |
| `--branch BRANCH` | The branch's name. Default: the git checkout's. | `OSH_BRANCH` |
| `--yes`, `-y` | Don't ask for confirmation. Required when stdin or stderr isn't a terminal (scripts, CI). | |
| `--watch` | Then watch the new build and exit with its result, like `osh builds watch`. | |
| `--timeout SECONDS` | Longest wait for the new build, with `--watch` only. Default `1800`. | |

- It first prints what it will rebuild (project, branch + stage, current latest build) and asks
  `y/yes`. Anything else sends nothing and exits 1.
- The request is sent **once, never retried**. If Odoo.sh doesn't confirm it, it exits 10: check
  `osh builds list` before running it again, or you may start two builds.

```bash
osh builds rebuild --branch staging --watch
osh builds rebuild --project acme --branch feature-x --yes --watch     # in a script
```

### `osh logs`

Print the end of a build's log, the whole log, or **follow it live**.

| Option | Description | Env var |
| --- | --- | --- |
| `--project PROJECT` | The project's name. Default: the git checkout's. | `OSH_PROJECT` |
| `--branch BRANCH` | The branch's name. Default: the git checkout's. | `OSH_BRANCH` |
| `--build BUILD_ID` | A build among the branch's latest ones. Default: the latest. | |
| `--kind NAME` | Which log, by name: `install`, `odoo`, `update`, `pip`, `neutralize`, `upgrade`, or any name `--kinds` shows (e.g. `pg_slow_queries`). Default: `install` if the build has it, else `odoo`. | |
| `--kinds` | List the build's logs (kind, name, size, last written) and print none. Doesn't combine with the printing options. | |
| `--tail LINES` | Print the last LINES lines (≥ 1), out of the log's last MiB. Default `100`. | |
| `--all` | Print the whole log from the first line. Not with `--tail`. | |
| `--follow`, `-f` | Keep printing new lines as they are written (polls every second) until Ctrl+C. With `--all`, starts from the first line. | |
| `--strip` / `--no-strip` | Remove / keep escape sequences and control characters. Default: removed when stdout is a terminal, kept when piped. | |
| `--timeout SECONDS` | Stop following after SECONDS and exit 8. `--follow` only. Default: no limit. | |

Details:

- Log text goes to **stdout only**, never stderr, and each line is flushed as printed, so it pipes
  well into `grep`, `tee`, `less`.
- On a terminal, escape sequences are stripped so a log line cannot clear your screen or retitle
  your window.
- Lines over 64 KiB are cut; invalid UTF-8 is replaced.
- `osh logs | head` ends quietly with exit 141 when the pipe closes.
- A log the build doesn't have exits 4 and names the ones it has. A build still waiting for a
  worker has no logs yet.

```bash
osh logs                                          # install (or odoo) log, last 100 lines
osh logs --kinds                                  # which logs exist on this build
osh logs --kind odoo --tail 500
osh logs --kind odoo -f                           # live tail until Ctrl+C
osh logs --kind odoo -f --timeout 600             # live tail for 10 minutes
osh logs --kind pg_slow_queries --tail 50         # PostgreSQL slow query log
osh logs --kind update --all > update.log         # whole update log to a file
osh logs --build 1233 --kind install              # a previous build
osh logs --kind odoo -f | grep --line-buffered -E "ERROR|CRITICAL|Traceback"
```

### `osh ssh`

Open a shell on the branch's latest build with **your own** `ssh`, its config, agent and keys
(the keys registered on your Odoo.sh account). `osh` reads no key; its process becomes `ssh`, so the
exit code is `ssh`'s.

| Argument / option | Description | Env var |
| --- | --- | --- |
| `--project PROJECT` | The project's name. Default: the git checkout's. | `OSH_PROJECT` |
| `--branch BRANCH` | The branch's name. Default: the git checkout's. | `OSH_BRANCH` |
| `[SSH_ARGS]...` | Everything after the options goes to `ssh` after the host: a remote command or `ssh` options. Put `--` first when it starts with a dash. | |

```bash
osh ssh                                         # interactive shell on the latest build
osh ssh --branch staging -- ls ~/src/user       # run one command
osh ssh -- -L 8069:localhost:8069               # forward a port
osh ssh --branch staging -- odoo-bin shell      # Odoo shell on staging
```

- Production and staging are behind the project firewall: use `osh firewall allow` first if your
  IP isn't allowed. Development builds aren't.
- On native Windows (no `ssh` to hand over to) it prints the command to run and exits 14. Use WSL.

### `osh firewall`

The IPv4 addresses allowed to reach a project's **production and staging** builds by SSH (and
PostgreSQL on a dedicated server). Odoo.sh accepts at most 10 entries and can take up to 5 minutes
to apply a change.

#### `osh firewall list`

| Option | Description | Env var |
| --- | --- | --- |
| `--project PROJECT` | The project's name. Default: the git checkout's. | `OSH_PROJECT` |

```bash
osh firewall list --project acme
```

#### `osh firewall allow [ADDRESS]`

**Changes state on Odoo.sh.** Allows an IPv4 address or CIDR range.

| Argument / option | Description | Env var |
| --- | --- | --- |
| `ADDRESS` | Public IPv4 address or CIDR range (e.g. `8.8.4.0/24`). Default: your public address, looked up at `https://api.ipify.org`. | |
| `--project PROJECT` | The project's name. Default: the git checkout's. | `OSH_PROJECT` |
| `--add` | Add to the existing entries. **Default is to replace them** so the address is the only one left. | |
| `--yes`, `-y` | Don't ask. Required when stdin or stderr isn't a terminal. | |

```bash
osh firewall allow --add                 # add my current public IP
osh firewall allow                       # my IP becomes the ONLY one (removes the others)
osh firewall allow 203.0.113.7 --add
osh firewall allow 198.51.100.0/24 --add --yes
```

- It shows the current and the resulting list, then asks. Nothing is sent if nothing would change.
- Private addresses exit 2; going past 10 entries exits 9 — both before anything is sent.

## Tutorials

All tutorials use a project named `acme`. Replace it with a name from `osh projects list`.

### 1. Push, watch the build, read why it failed

From a checkout of the repository the project builds, on branch `feature-x`:

```bash
git commit -am "[FIX] sale: fix discount rounding"
git push
osh builds watch
```

You see a live status line, then:

```text
Build 88212 failed: https://acme-feature-x-88212.dev.odoo.com
```

The exit code is 20, so you can chain the next step:

```bash
osh logs --tail 200                    # the install log, where tests run
osh logs --all | grep -n -B2 -A30 "Traceback"
```

Put it in one alias in your `~/.bashrc`:

```bash
alias ship='git push && (osh builds watch || osh logs --tail 200)'
```

### 2. Watch the production server log live

```bash
osh logs --project acme --branch production --kind odoo -f
```

Useful filters while following (use `--line-buffered` so `grep` prints immediately):

```bash
# Only errors and warnings
osh logs --project acme --branch production --kind odoo -f \
  | grep --line-buffered -E " (ERROR|WARNING|CRITICAL) "

# Only one model's RPC calls
osh logs --project acme --branch production --kind odoo -f \
  | grep --line-buffered "account.move"

# Requests slower than 5 seconds of SQL: the werkzeug line ends with
# "<queries> <sql_seconds> <other_seconds>"
osh logs --project acme --branch production --kind odoo -f \
  | awk '/werkzeug/ { if ($(NF-1)+0 > 5) { print; fflush() } }'

# Keep a copy while watching
osh logs --project acme --branch production --kind odoo -f | tee prod-$(date +%F).log
```

And the PostgreSQL slow-query log:

```bash
osh logs --project acme --branch production --kinds
osh logs --project acme --branch production --kind pg_slow_queries --tail 100
```

### 3. Rebuild staging and wait for it

```bash
osh builds rebuild --project acme --branch staging --watch
```

```text
Project: acme
Branch: staging (staging)
Latest build: 88190 ...
Start a new build of this branch? [y/N]: y
Build 88230 was started.
...
Build 88230 succeeded: https://acme-staging-88230.dev.odoo.com
```

If the watch fails or times out, the new build keeps running: follow it again with
`osh builds watch 88230` instead of rebuilding a second time.

### 4. A shell, the Odoo shell and a database tunnel

```bash
osh firewall allow --add --project acme          # once, if you work from a new IP (staging/prod)
osh ssh --project acme --branch staging          # interactive shell
osh ssh --project acme --branch staging -- 'tail -n 50 ~/logs/odoo.log'
osh ssh --project acme --branch feature-x -- odoo-bin shell
```

### 5. Work from a new network (firewall)

```bash
osh firewall list --project acme
osh firewall allow --add --project acme      # adds your current public IP
# ... up to 5 minutes later, SSH to production/staging works from here
```

Prefer `--add`: without it, your IP **replaces** every other entry (colleagues, office, CI).

### 6. Scripts and CI

On CI there is no keyring and no browser. Store the `session_id` cookie as a secret and expose it
as `ODOUCHE_SESSION`; it is kept in memory only.

GitHub Actions — wait for the Odoo.sh build of the pushed commit, and print the log if it failed:

```yaml
name: odoo.sh build
on: push
jobs:
  wait:
    runs-on: ubuntu-latest
    steps:
      - uses: astral-sh/setup-uv@v6
      - name: Install osh
        run: uv tool install "git+https://github.com/abdallah-abdelsabour/odoo-sh-toolkit#subdirectory=packages/odouche-cli"
      - name: Wait for the build
        env:
          ODOUCHE_SESSION: ${{ secrets.ODOUCHE_SESSION }}
        run: |
          osh builds watch --project acme --branch "${GITHUB_REF_NAME}" \
            --commit "${GITHUB_SHA}" --timeout 3600 \
            || { osh logs --project acme --branch "${GITHUB_REF_NAME}" --tail 300; exit 1; }
```

A shell script that reacts to the exit code:

```bash
#!/usr/bin/env bash
osh builds watch --project acme --branch staging --no-wait
case $? in
  0)  echo "green" ;;
  20) echo "failed"; osh logs --project acme --branch staging --tail 100 ;;
  21) echo "finished with warnings" ;;
  23) echo "still running after the timeout" ;;
  3)  echo "session expired: run osh auth login" ;;
  *)  echo "other error" ;;
esac
```

Odoo.sh sessions last at most 30 days in `osh`: refresh the CI secret before then.

### 7. JSON recipes with `jq`

```bash
# All projects and their repositories
osh --format json projects list | jq -r '.[] | "\(.name)\t\(.repository)"'

# Result of the latest build on every staging branch of a project
for b in $(osh --format json branches list --project acme --stage staging | jq -r '.[].name'); do
  printf '%s: ' "$b"
  osh --format json builds show --project acme --branch "$b" | jq -r '.result'
done

# Who pushed what on production
osh --format json builds list --project acme --branch production \
  | jq -r '.[] | "\(.started_at)  \(.commit.author)  \(.commit.message)"'

# The database URL of the latest build
osh --format json builds show --project acme --branch feature-x | jq -r .url

# Log lines as JSON objects {text, offset, truncated}
osh --format json logs --kind odoo --tail 20 | jq -r .text
```

### 8. Morning check across all your client projects

```bash
for p in $(osh --format json projects list | jq -r '.[].name'); do
  r=$(osh --format json builds show --project "$p" --branch production 2>/dev/null | jq -r '.result // "?"')
  printf '%-30s %s\n' "$p" "$r"
done
```

## Using it with AI agents (MCP server)

`odouche-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server over stdio.

### Step 1 — log in (the server can't)

```bash
osh auth login
```

The server reads the session from the keyring **on every call**, so a login done while the server
is running is picked up without a restart. Where there is no keyring, start the server with
`ODOUCHE_SESSION` in its environment (never write the value in a config file).

### Step 2 — register the server in your client

**Claude Code:**

```bash
claude mcp add odouche -- uvx --from "git+https://github.com/abdallah-abdelsabour/odoo-sh-toolkit#subdirectory=packages/odouche-mcp" odouche-mcp
```

**Project-wide `.mcp.json`** (Claude Code, and other clients that read this format):

```json
{
  "mcpServers": {
    "odouche": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/abdallah-abdelsabour/odoo-sh-toolkit#subdirectory=packages/odouche-mcp",
        "odouche-mcp"
      ]
    }
  }
}
```

**Cursor** (`~/.cursor/mcp.json`), **Windsurf**, **Claude Desktop** and others: the same
`command` + `args` in that client's MCP config.

**From a local clone** (fastest start, and your local changes are used):

```json
{
  "mcpServers": {
    "odouche": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/odoo-sh-toolkit", "odouche-mcp"]
    }
  }
}
```

**WSL:** if your AI client runs on Windows but `osh` and its keyring are in WSL, run the server
inside WSL:

```json
{
  "mcpServers": {
    "odouche": {
      "command": "wsl.exe",
      "args": ["-d", "Ubuntu-24.04", "--", "bash", "-lc", "odouche-mcp"]
    }
  }
}
```

(after `uv tool install --editable ./packages/odouche-mcp --with-editable ./packages/odouche` inside
WSL, so `odouche-mcp` is on the `PATH`).

Name the server `odouche`: the optional Claude Code hook below matches on that name. For a
reproducible setup, pin a commit: `...odoo-sh-toolkit@<commit-sha>#subdirectory=...`.

### Step 3 — tools the agent gets

| Tool | Changes Odoo.sh | Arguments | Returns |
| --- | --- | --- | --- |
| `get_session` | No | — | Whether a session is available, its source, user, seconds left. Never the session itself. |
| `list_projects` | No | `limit` (50, max 200) | Projects: name, repository, URL; `truncated`. **Call first**: every other tool takes the project name. |
| `list_branches` | No | `project`, `limit` (50, max 200) | Branches with their stage. |
| `list_builds` | No | `project`, `branch`, `limit` (4, max 20) | Latest builds, newest first: status, result, commit. |
| `get_build` | No | `project`, `branch`, `build_id`? | One build (latest by default) with commit and database URL. |
| `wait_for_build` | No | `project`, `branch`, `build_id`? or `commit`?, `timeout` (30 s, max 50) | The build as last seen, `finished`, `next_step` ("call again") while running. |
| `read_log` | No | `project`, `branch`, `build_id`?, `kind`?, `lines` (100, max 500), `contains`? | `untrusted_lines` (control chars removed), `truncated`. `contains` is a plain, case-sensitive substring filter. |
| `get_firewall` | No | `project` | The allowed IPv4 addresses / CIDR ranges. |
| `rebuild_branch` | **Yes** (only with `--allow-changes`) | `project`, `branch` | The new in-progress build. Development/staging only; sent once, never retried. |
| `update_firewall` | **Yes** (only with `--allow-changes`) | `project`, `address`, `replace` (false) | The stored firewall and `changed`. **Adds** by default (unlike the CLI). Idempotent. |

Full details: [docs/mcp-reference.md](docs/mcp-reference.md) and [docs/mcp.md](docs/mcp.md).

### Read-only by default, changes are opt-in

Started as above, the server can only read. To let an agent rebuild a branch or edit the firewall,
add the flag yourself:

```json
"args": ["--from", "git+https://github.com/abdallah-abdelsabour/odoo-sh-toolkit#subdirectory=packages/odouche-mcp", "odouche-mcp", "--allow-changes"]
```

- The flag is read once at start-up. No tool or argument can turn it on during a session.
- Without it, `rebuild_branch` and `update_firewall` don't even exist for the agent.
- With it, both carry the MCP *destructive* hint, so clients that ask before destructive tools ask.
- Each change call writes one audit line to the server's stderr (tool, target, outcome; never the
  session).

**Claude Code hook:** auto-approve the read-only tools and keep the normal prompt for anything
else. Copy [`examples/claude-code/odouche_read_only_hook.py`](examples/claude-code/odouche_read_only_hook.py)
into `.claude/hooks/` and add to `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "mcp__odouche__.*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/odouche_read_only_hook.py",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

### Example prompts

Things you can ask an agent once the server is connected:

- *"List the branches of project acme and tell me which development builds are failing."*
- *"I just pushed commit 3f2a9c1 to feature-x on acme. Wait for the build, and if it fails, read
  the install log, find the traceback and fix the module."*
- *"Read the last 500 lines of the odoo log on acme production that contain `ERROR` and group them
  by cause."*
- *"Which requests in the acme production log took more than 10 seconds of SQL? Find the Python
  code that serves them in this repo."*
- *"Show me the pg_slow_queries log of acme production and suggest indexes."*
- *"Compare the latest build of staging and production on acme: which commits are on staging only?"*
- *(with `--allow-changes`)* *"Rebuild acme staging and tell me when it's green."*

### A typical autonomous fix loop

```text
agent: edits code → git commit → git push
agent: wait_for_build(project="acme", branch="feature-x", commit="<sha>")   # repeats while next_step says so
       → finished=true, result="failed"
agent: read_log(project="acme", branch="feature-x", kind="install", lines=300, contains="Error")
       → untrusted_lines: [... "psycopg2.errors.NotNullViolation: null value in column ..." ...]
agent: fixes the field default → pushes → wait_for_build(...) → result="success"
```

### Agent safety notes

- Build logs are text written by anyone who can make the instance print something. They are
  returned as `untrusted_lines` and are **not** instructions; they can also contain secrets of the
  instance, which are not masked. That's why the server is read-only by default.
- The project is always an explicit argument; the server never guesses it from a working directory.
- No SSH tool exists in the MCP server, with or without `--allow-changes`.
- An agent with a shell can also use `osh` directly; give it `--format json` and the
  [exit codes](#exit-codes).

## Using it from Python (library)

```bash
uv add "odouche @ git+https://github.com/abdallah-abdelsabour/odoo-sh-toolkit#subdirectory=packages/odouche"
```

```python
import odouche

with odouche.Client() as client:               # session from ODOUCHE_SESSION or the keyring
    for project in client.projects():
        print(project.name, project.repository, project.url)

    branches = {b.name: b for b in client.branches("acme")}
    branch = branches["staging"]
    build = client.latest_build(branch)
    print(build.id, build.status, build.result, build.commit.message, build.url)

    # The last 20 lines of the install log
    for line in client.read_log("acme", build, odouche.LogKind.INSTALL, tail=20):
        print(line.text)

    # Follow the server log for 10 minutes
    try:
        for line in client.follow_log("acme", build, odouche.LogKind.ODOO, timeout=600):
            if " ERROR " in line.text:
                print(line.text)
    except odouche.StreamTimeoutError:
        pass

    # Watch a build until it finishes
    for build in client.watch_build("acme", build, timeout=1800):
        print(build.status, build.result)
```

- Synchronous API, typed frozen dataclasses (`Project`, `Branch`, `Build`, `Commit`, `Log`,
  `LogLine`, `Firewall`, …), ships `py.typed`.
- `odouche.Client(read_only=True)` refuses `rebuild` and `allow_ip` before sending anything.
- Errors are typed: `NoSessionError`, `SessionExpiredError`, `NotFoundError`,
  `UpstreamChangedError`, `StreamTimeoutError`, `ReadOnlyError`, `StageRefusedError`, …

Full guide: [docs/library.md](docs/library.md), API reference: [docs/reference.md](docs/reference.md).

## Output formats and scripting

- `--format table` (default) or `--format json`, **before** the command.
- Only the result goes to stdout; progress, prompts and errors go to stderr. `osh --format json …
  | jq` receives nothing else.
- JSON is the library's model, keys are its attribute names; datetimes are ISO 8601 with offset.

| Command | JSON on stdout |
| --- | --- |
| `osh auth status` | One `SessionInfo` |
| `osh auth status --check` | One `Identity` (with `session`) |
| `osh projects list` | Array of `Project` |
| `osh branches list` | Array of `Branch` |
| `osh builds list` | Array of `Build` |
| `osh builds show`, `osh builds rebuild` | One `Build` |
| `osh builds watch`, `rebuild --watch` | One `Build` per line per change (result line on stderr) |
| `osh logs` | One `LogLine` per line: `{"text", "offset", "truncated"}` |
| `osh logs --kinds` | Array of `Log` |

Example `Build`:

```json
{
  "id": 88212,
  "name": "acme-feature-x-88212",
  "branch_id": 51044,
  "branch_name": "feature-x",
  "commit": {
    "hash": "3f2a9c1e...",
    "message": "[FIX] sale: fix discount rounding",
    "author": "Jane Dev",
    "timestamp": "2026-10-13T20:22:10+00:00",
    "url": "https://github.com/acme/acme/commit/3f2a9c1e..."
  },
  "status": "done",
  "status_name": "done",
  "result": "failed",
  "result_name": "failed",
  "status_info": "",
  "started_at": "2026-10-13T20:23:01+00:00",
  "url": "https://acme-feature-x-88212.dev.odoo.com"
}
```

- Empty results: `[]` in JSON, one line on stderr in a table; both exit 0.
- Tables have no colour or box drawing when stdout isn't a terminal or `NO_COLOR` is set.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | Unexpected error (a bug: please report), or a declined confirmation. |
| 2 | Usage error: bad option/value, conflicting options, no project or branch found, invalid IP, or a change that needs `--yes` outside a terminal. |
| 3 | No session or an expired one → `osh auth login`. |
| 4 | Project, branch, build or log not found / not reachable by you. |
| 5 | The session is not allowed to do this. |
| 6 | Odoo.sh answered in a shape `osh` doesn't understand (upstream changed). Please report. |
| 7 | Odoo.sh unreachable, or a server error. |
| 8 | A followed log (`--timeout`) or a login reached its timeout. |
| 9 | Refused, nothing changed: read-only, a stage that can't be rebuilt (production), or a firewall change Odoo.sh doesn't take. |
| 10 | A change was sent but not confirmed → check `osh builds list` / `osh firewall list` before retrying. |
| 11 | No usable keyring. |
| 12 | Login ended without a session. |
| 13 | Other library error. |
| 14 | `osh ssh`: no `ssh` to hand over to (native Windows); the command is printed. |
| 15 | `osh firewall allow`: public IP not found; give `ADDRESS`. |
| 20 | Build failed → `osh logs`. |
| 21 | Build finished with warnings. |
| 22 | Build ended without a result (dropped, killed, skipped, unknown). |
| 23 | Build not finished / not appeared at the timeout. |
| 130 | Interrupted (Ctrl+C). |
| 141 | Stdout pipe closed by its reader (e.g. `head`). |

`osh ssh`, once it has become `ssh`, exits with `ssh`'s own code (the remote command's, or 255).

## Environment variables

| Variable | What it does |
| --- | --- |
| `ODOUCHE_SESSION` | The Odoo.sh session (`session_id` cookie of `www.odoo.sh`). Used instead of the keyring, kept in memory only. For CI, containers, and machines without a keyring. |
| `OSH_PROJECT` | Default project when `--project` isn't given. |
| `OSH_BRANCH` | Default branch when `--branch` isn't given. |
| `OSH_DEBUG` | `1` is the same as `--debug`. |
| `NO_COLOR` | Non-empty: tables without colour or box drawing. |
| `DISPLAY`, `WAYLAND_DISPLAY` | On Linux, with neither set, `osh auth login` asks for the cookie instead of opening a browser. |
| `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY` | Proxy for requests to Odoo.sh (lowercase names too). `HTTP_PROXY` has no effect: everything is HTTPS. |
| `SSL_CERT_FILE`, `SSL_CERT_DIR` | Trusted CAs instead of the system's. |

## Security model

- Your **password and GitHub token are never seen**: you sign in in a browser, as on odoo.sh.
- Only the resulting **Odoo.sh session** is kept, in the OS keyring (Secret Service on Linux/WSL,
  Keychain on macOS, Credential Locker on Windows). Never in a plain-text file. Plain-text keyring
  backends are refused.
- Sessions expire when Odoo.sh says so, and at most **30 days** after login on the client side.
  They are never refreshed. `osh auth logout` ends the session on Odoo.sh too.
- The session is sent only to `www.odoo.sh`. Logs are fetched from the build's worker with the
  project's access token, which is fetched per call and kept only in memory.
- Session values never appear in logs, exceptions, output or MCP results.
- Only two operations change anything: a rebuild (dev/staging only) and a firewall change. Both
  are sent once and never retried, and both ask first in the CLI.
- `osh firewall allow` without an address calls `https://api.ipify.org`. That's the only request
  sent anywhere other than Odoo.sh, its workers and the login browser.

Details: [docs/security.md](docs/security.md). Report vulnerabilities privately (see
[SECURITY.md](SECURITY.md)).

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Not logged in. Run osh auth login.` (exit 3) | `osh auth login`. Sessions expire after 30 days at most, or earlier if Odoo.sh ends them. |
| No usable keyring (exit 11), common on WSL/servers | Install a Secret Service provider (`gnome-keyring`), or `export ODOUCHE_SESSION=...` (from your browser's `session_id` cookie on `www.odoo.sh`). |
| No browser opens on login | Expected on SSH, Windows, or Linux without `DISPLAY`: paste the `session_id` cookie when asked. |
| "Several projects build this repository" (exit 2) | Add `--project NAME` or `export OSH_PROJECT=NAME`. |
| "No project builds this repository" (exit 4) | You're in a repo Odoo.sh doesn't build, or with another remote: use `--project`. |
| `osh builds watch` exits 23 right after `git push` | The build of HEAD didn't appear within 2 minutes: check the push reached GitHub, or use `--no-wait`. |
| `osh logs` says the log doesn't exist (exit 4) | `osh logs --kinds` lists the ones the build has. Staging/production have no `install` log: use `--kind odoo` or `--kind update`. |
| `osh ssh` hangs or is refused on staging/production | Your IP isn't in the firewall: `osh firewall allow --add`, wait up to 5 minutes. Check your SSH key is on your Odoo.sh account. |
| `osh ssh` exits 14 | Native Windows: use WSL, or run the command it prints. |
| Odoo.sh changed shape (exit 6) | Odoo.sh changed its internal API. Pull the latest version; open an issue with the request and field named in the error. |
| MCP tool missing (`rebuild_branch`, `update_firewall`) | Start the server with `--allow-changes`. |
| See what's going on | Add `--debug` (or `OSH_DEBUG=1`) to any command. |

## Development

Requires [uv](https://docs.astral.sh/uv/) and `make`.

```bash
git clone https://github.com/abdallah-abdelsabour/odoo-sh-toolkit.git
cd odoo-sh-toolkit
make install   # install the workspace (editable) and the git hooks
make test      # run the test suite (no network: Odoo.sh is mocked)
make check     # format, lint, types, dependencies, dead code, tests with coverage, docs
make help      # every target
uv run osh --help              # run the CLI from the workspace
uv run odouche-mcp             # run the MCP server from the workspace
```

- Layout: `packages/odouche` (library; everything Odoo.sh-specific lives in
  `src/odouche/_upstream/`), `packages/odouche-cli` (the `osh` commands, one module per group),
  `packages/odouche-mcp` (the MCP tools).
- A new capability goes into the library first, then into the CLI and/or the MCP server.
- `make docs-cli` and `make docs-mcp` regenerate the reference pages from the code.
- `make live-check PROJECT=<name>` checks the read path against the real Odoo.sh.

Conventions: [CONTRIBUTING.md](CONTRIBUTING.md). How the Odoo.sh endpoints were mapped:
[docs/upstream.md](docs/upstream.md).

## Credits and original project

This repository is based on **[odouche](https://github.com/avanserv/odouche)** by
**[Avanserv](https://github.com/avanserv)**, the original project, published under the MIT
license. All credit for the design and implementation of the library, the `osh` CLI and the MCP
server goes to its authors.

This repository republishes it as a single snapshot (including the firewall commands and MCP
firewall tools), with this extended README, tutorials and AI-agent guide. For the upstream history,
releases on PyPI (`uv tool install odouche-cli`, `uvx odouche-mcp`), issues and the documentation
site, see the original:

- Original repository: <https://github.com/avanserv/odouche>
- Original documentation: <https://avanserv.github.io/odouche/>

## License

[MIT](LICENSE) — Copyright (c) 2026 Avanserv. The original copyright and license notice are kept
as the MIT license requires.
