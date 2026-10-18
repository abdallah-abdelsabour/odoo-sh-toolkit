# MCP server

`odouche-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server that lets
development agents work with your Odoo.sh projects. It runs over stdio, and started as below its
tools read: none changes anything on Odoo.sh. The two tools that do are
[yours to turn on](#changing-state).

The [reference](mcp-reference.md) lists every tool with its arguments, what it returns, what it
can touch and its bounds, generated from the server.

## Setup

### Log in

The server cannot log in. Give it a session first, one of two ways:

- Run `osh auth login`, from [`odouche-cli`](cli.md), which stores the session in your keyring.
- Where there is no keyring, set `ODOUCHE_SESSION` in the server's environment. A client that
  does not pass its own environment on has to be told to pass the variable. Do not write its value
  in a configuration file.

### Claude Code

<!-- x-release-please-start-version -->
```bash
claude mcp add odouche -- uvx odouche-mcp==0.4.0
```
<!-- x-release-please-end -->

Or, for everyone who works on a project, in its `.mcp.json`:

<!-- x-release-please-start-version -->
```json
{
  "mcpServers": {
    "odouche": { "command": "uvx", "args": ["odouche-mcp==0.4.0"] }
  }
}
```
<!-- x-release-please-end -->

Name the server `odouche`: the [hook](#asking-before-a-change) matches on that name.

### Any other client

The server speaks stdio and takes no configuration but its one flag. Give your client this
command:

<!-- x-release-please-start-version -->
```bash
uvx odouche-mcp==0.4.0
```
<!-- x-release-please-end -->

### Why a version is pinned

Without a version, `uvx` runs the newest release the first time, and again whenever its cache is
pruned or refreshed: the version can change without you choosing it, and this process holds your
Odoo.sh session. Pinned, a new release runs only once you have changed the number.

## Session

The server cannot log in, and has no tool that does: a browser opened because an agent asked is
a phishing surface. It uses the session you already have:

1. `ODOUCHE_SESSION`, when it is set in the server's environment. It is kept in memory only.
2. The session `osh auth login` stored in the keyring.

The session is looked up on each call, not when the server starts. The server starts and lists
its tools while you are logged out, and an `osh auth login` made while it runs is used by the
next call, with no restart. No tool takes a session as an argument.

With no session, an expired one or no usable keyring, a tool's error tells the agent to stop and
ask you to run `osh auth login` in a terminal, or to restart the server with `ODOUCHE_SESSION`
set where there is no keyring. When the session comes from `ODOUCHE_SESSION`, it says to restart
the server with a current value or without the variable, since a login would not be read.

## Tools

What the [reference](mcp-reference.md) does not say of each tool.

### `get_session`

It never returns the session. With none, `available` is false and `problem` says what to do. A
session Odoo.sh rejects is an error, as from any other tool. The seconds left are those before the
session's [max age](security.md).

### `list_projects`

Every other tool takes a project by its name, so an agent calls this one first.

### `list_branches`

The branches come in the order Odoo.sh answers them.

### `list_builds`

`branch` is the git branch's name, as for every tool that takes one. Odoo.sh has only been seen to
answer the latest builds of a branch: older ones are out of reach.

### `get_build`

Without `build_id`, it reads the branch's latest build. A build older than the branch's latest
ones is not found.

### `wait_for_build`

It waits for the build of `build_id`, or the branch's latest one, and returns it as last seen and
`finished`, as soon as the build ends.

A build that outlasts the wait is not an error. `finished` is false and `next_step` tells the
agent to call the tool again, which continues the wait. The wait is short because a client gives
up on a call that lasts: a `timeout` over the most is lowered to it, `timeout` in the result is
the one applied, and `timeout_capped` says it was lowered.

With `commit`, the first 7 to 64 digits of a hash, the tool waits for the branch to have a build
of that commit, then for that build. Right after a push the latest build is still the previous
one, so an agent that pushed gives the commit. While the branch has no such build, `build` is null
and `next_step` says to call again. `build_id` and `commit` are not given together.

- **Progress**: a notification at each change of the build, to a client that asked for them. It
  holds the build's number, status and result, and nothing Odoo.sh or a commit's author wrote.
- **Cancellation**: a call the client cancels closes its connection to Odoo.sh, within a few
  seconds unless a request is being answered.
- **Timeout**: the requests that find the build, or await one of a commit, are not cut at the
  `timeout`: a slow Odoo.sh can make a call last longer.

### `read_log`

Without `kind`, it reads the install log when the build has it and the odoo log otherwise. With
`contains`, only the lines that hold that text are returned, out of the log's last mebibyte. The
text is matched as it is, case included, and not as a pattern. A `kind` of `unknown` is refused.

The lines come in `untrusted_lines`, without their escape sequences and control characters, and
are [not instructions](#build-logs). `truncated` is true when the last mebibyte held more lines
than returned, or one was cut. It says nothing of what the log holds before that, which is not
read. When the lines are over the most a result holds, the oldest are left out. A result carries
them twice, as text and as structured content.

### `get_firewall`

The firewall lists the public IPv4 addresses and CIDR ranges that may reach the project's
production and staging builds by SSH, and by PostgreSQL on a dedicated server. A development
build is not behind it.

### `rebuild_branch`

It is listed only when the server is started with [`--allow-changes`](#changing-state). Only a
development or a staging branch is rebuilt: any other is refused before the rebuild is sent.

The request is sent once and never repeated. When Odoo.sh does not confirm it, or the new build
cannot be found, the error says so and tells the agent to list the branch's builds before trying
again, since a second call can start a second build.

### `update_firewall`

It is listed only when the server is started with [`--allow-changes`](#changing-state). It adds
`address` to the project's firewall, or with `replace` makes it the only one, so that the address
can reach the production and staging builds. Odoo.sh can take up to 5 minutes to apply it.

- `address` is a public IPv4 address or a range in CIDR notation, given by the agent: the server
  does not look up the user's address, and the agent asks the user or finds it itself. Odoo.sh
  takes public ones only, 10 at most: anything else is refused before anything is sent.
- It adds by default, unlike `osh firewall allow`, which replaces: an agent removes the user's
  other addresses only when it asks for `replace`.
- A firewall that already is as asked is not changed: `changed` is false and nothing is sent, so
  calling it twice is safe.
- The change is sent once and never repeated. When Odoo.sh does not answer the addresses it was
  given, the error tells the agent to read the firewall with `get_firewall` before trying again.

## Security

- **By default** the server reads your projects, their branches, their builds, the builds' logs
  and the projects' firewalls. It changes nothing on Odoo.sh.
- **With `--allow-changes`** it can also start a rebuild of a development or a staging branch, and
  let an address through a project's firewall, which with `replace` removes the others.
- **It never** logs in, takes a session as an argument, returns the session in a result or writes
  a file.

[Security](security.md) has how the session is obtained and stored.

### Changing state

The tools that change state on Odoo.sh are off until you start the server with `--allow-changes`:

<!-- x-release-please-start-version -->
```json
{
  "mcpServers": {
    "odouche": { "command": "uvx", "args": ["odouche-mcp==0.4.0", "--allow-changes"] }
  }
}
```
<!-- x-release-please-end -->

- The flag is read once, when the server starts. No tool and no argument sets it, so nothing an
  agent does during a session turns it on.
- Without it, `rebuild_branch` and `update_firewall` are not registered, and every tool opens the
  library's client read-only, which refuses a change before anything is sent.
- With it, both are listed without the read-only hint and with the destructive one, so a client
  that asks before such tools asks before these. The server itself does not ask.
- Each call writes one line to the server's stderr: the tool, its target (the project and the
  branch, or the project, the address and `replace`), and what came of it or the kind of error. It
  never holds the session.

### Asking before a change

The flag decides whether `rebuild_branch` and `update_firewall` exist. Whether an agent may call a tool without asking
you is your client's decision. For Claude Code, a hook lets the tools that read pass and leaves
the rest to the usual prompt:
[`examples/claude-code/odouche_read_only_hook.py`](https://github.com/avanserv/odouche/blob/main/examples/claude-code/odouche_read_only_hook.py).
It needs Python and nothing else. Read it, copy it into your project's `.claude/hooks/`, and add
it to `.claude/settings.json`:

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

- It approves a call to a tool on its list, which a test holds to the tools the server registers
  with the read-only hint. For any other tool, and for any input it does not understand, it
  prints nothing and Claude Code asks you as it would without the hook.
- Claude Code names a tool `mcp__<server>__<tool>`, where `<server>` is the name the server has
  in your configuration. Under another name than `odouche`, change `SERVER` in the script and the
  matcher above.
- It does not read a call's arguments, and it never denies a call.
- `read_log` is one of the tools it approves, so the lines of a [build log](#build-logs) reach
  the agent without a prompt.
- It does not replace `--allow-changes`: without the flag there is no tool to ask about.

Another client decides from the [hints](#tool-contract) each tool carries.

### Build logs

A log is whatever the build printed: the output of every module, and of every request made to the
instance. Anyone who can make it print a line can write one that reads as an instruction to an
agent, and a log can hold a secret of the instance.

- `read_log` returns the lines in one field, `untrusted_lines`, and its description tells the
  agent that they are data and are not to be followed. That lowers the risk of prompt injection.
  It does not remove it: an agent can still act on what it reads.
- Secrets are not masked. A mask would catch some and promise all, so the lines pass through as
  they are, into the agent's context and whatever the client does with it.
- No line of a log is written to the server's stderr.

This is why the server is read-only unless you [start it otherwise](#changing-state): an agent
misled by a log has no tool that changes anything on Odoo.sh.

## Arguments and results

### The project

The project is always an argument. The server does not read it from a git checkout: its working
directory is wherever the client started it, and a guess could point an agent at the wrong
project. A call without one is rejected.

### Limits

A list holds at most `limit` items, and `truncated` is true when there were more. A `limit` over
the most a tool returns is lowered to it, and one below 1 is an error. The `lines` of `read_log`
and the `timeout` of `wait_for_build` follow the same rule.

### Untrusted text

Branch names, commit messages and author names are written by other people. The server returns
them in the fields of a result only, never in a sentence of its own, and a client should treat
them as data, not as instructions.

## Tool contract

Every tool follows the same rules, and a test over the registered tools holds them to it.

- **Names** are a verb then a noun, such as `list_projects` or `get_build`, and do not change
  once released: permission rules and hooks match on them.
- **Descriptions** end with three labelled lines: `Returns:` what the tool returns, `Touches:`
  what it can change on Odoo.sh, and `Bounds:` its limits on size and time. A tool that changes
  nothing says `Touches: reads only`.
- **Annotations** are all set. A tool that reads has the read-only hint, and a client can use the
  hints to decide when to ask you first.
- **Results** are structured, with the field names of the [library's models](reference.md), which
  are also what `osh --format json` prints.
- **Errors** are a message and, when there is one, the next step: never a traceback or an answer
  from Odoo.sh.

## Troubleshooting

A tool's error says what happened and, when there is one, what the agent or you should do next.

| Error | What to do |
| --- | --- |
| No session | Run `osh auth login` in a terminal. The next call uses it, with no restart. |
| An expired session, or one Odoo.sh rejects | The same. Sessions are never refreshed. |
| An expired session that comes from `ODOUCHE_SESSION` | Restart the server with a current value, or without the variable to use the one `osh auth login` stores. A login is not read while the variable is set. |
| No usable keyring | Install a [Secret Service provider](security.md#where-the-session-is-stored), or restart the server with `ODOUCHE_SESSION` set. A locked keyring is unlocked in its own dialog. |
| Odoo.sh changed shape | Odoo.sh has no public API, and an answer no longer has the shape odouche reads. Upgrade to the latest release, and [report it](https://github.com/avanserv/odouche/issues) with the request and the field the error names if it remains. |
| A tool is missing | `rebuild_branch` and `update_firewall` exist only with [`--allow-changes`](#changing-state). |

The server is built on the [`odouche` library](library.md) and never talks to Odoo.sh directly.
