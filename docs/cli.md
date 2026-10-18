# CLI

`osh` is a command-line interface for Odoo.sh, in the spirit of `gcloud`, `aws` and `scw`. It is
built on the [`odouche` library](library.md) and adds nothing of its own beyond presentation:
every command is a call into the library, rendered for a terminal.

This page is the guide. The [reference](cli-reference.md) has every command with its arguments,
options and defaults, as `osh <command> --help` prints them.

## Install

```bash
uv tool install odouche-cli
osh --version
```

To run it once without installing it:

```bash
uvx --from odouche-cli osh --help
```

## Sign in

```bash
osh auth login
```

- A browser window opens for you to sign in to Odoo.sh with GitHub. `osh` stores the session in
  your operating system's keyring and names the account. Where no browser can be launched, as over
  SSH, it asks for the `session_id` cookie in a prompt that does not echo it.
- [Security](security.md) has what is kept, where and for how long.
- [`osh auth status`](cli-reference.md#osh-auth-status) shows where the session comes from, how
  long ago it was stored and when it expires. It exits 3 when there is no usable session, so a
  script can test for one.
- [`osh auth logout`](cli-reference.md#osh-auth-logout) removes the session from the keyring and
  says whether Odoo.sh ended it.
- No option takes a session: arguments show in the process list and in the shell history. Where
  there is no keyring, as in CI, the session comes from
  [`ODOUCHE_SESSION`](#environment-variables).

## Push, watch, read the logs

From a checkout of the repository that the project `acme` builds, on the branch `feature-x`:

```bash
git push
osh builds watch
osh logs
```

- [`osh builds watch`](cli-reference.md#osh-builds-watch) waits for the build of the commit you
  pushed, follows it until it finishes and exits with [its result](#exit-codes). Its last line is
  the result and the address of the build's database:

    ```text
    Build 1234 failed: https://acme-feature-x-1234.dev.odoo.com
    ```

- [`osh logs`](cli-reference.md#osh-logs) prints the end of that build's `install` log, where a
  development build installs the modules and runs their tests. A build that has none, as on a
  staging branch, gives its `odoo` log. `osh logs --follow` keeps printing while the build runs.
- As one line: `git push && (osh builds watch || osh logs)`.
- Anywhere else, name the two: `osh builds watch --project acme --branch feature-x`.
- [`osh builds rebuild --watch`](#rebuilding-a-branch) starts a new build of the branch without a
  commit, and watches it.

## Project and branch

A command that works on a project or a branch takes them from the first of these that gives a
value:

| Order | Project | Branch |
| --- | --- | --- |
| 1 | `--project` | `--branch` |
| 2 | `OSH_PROJECT` | `OSH_BRANCH` |
| 3 | The git checkout's remote, when it is a GitHub repository | The git checkout's current branch |

- The project is given by its name, as `osh projects list` shows it.
- The remote is the upstream of the current branch, and `origin` otherwise. A detached HEAD
  gives no branch.
- A repository that several of your projects build is never guessed between: the command exits 2
  and lists them. One that none of them builds exits 4.
- With no source giving a value, or an empty `--project` or `--branch`, the command exits 2.
- `--debug` says on stderr which source was used.

## Watching a build

`osh builds watch` follows one build until it finishes: the one whose number is given, or the
build of the commit you just pushed.

- When the checkout is of the project's repository and on the branch, however the two were named,
  it waits for a build of the checkout's HEAD to be listed, asking every 3 seconds, and watches
  that one.
- `--commit` waits for another commit of the branch, also when the branch is not the one checked
  out.
- `--no-wait` watches the branch's latest build, whatever its commit, and so does a run with
  no `--commit` on another project or branch than the checkout's, or one where HEAD cannot be
  read.
- The wait for a commit's build lasts two minutes at most, then exits 23: push the commit, or use
  `--no-wait`. `--timeout` covers that wait and the build.
- The changes go to stderr: one line kept up to date when stderr is a terminal, with how long the
  build has run, and one line per change otherwise. The last line, on stdout, is the result and
  the address of the build's database.
- A build that was dropped after it finished exits with the result it had, and is said to have
  been replaced by a newer build. A dropped build has no address.
- Ctrl+C exits 130 and leaves the build running: watching changes nothing on Odoo.sh.

## Rebuilding a branch

`osh builds rebuild` starts a new build of the branch, which replaces its latest one. With
[`osh firewall allow`](#letting-your-address-through-the-firewall), it is one of the two commands
that change a project: every other command that opens a client opens it read-only, and it sends
no change.

- Only a development or a staging branch is rebuilt. Any other stage exits 9 before anything is
  asked or sent.
- It first says on stderr what it works on: the project, the branch with its stage, and the
  branch's latest build with its commit. Then it asks on stderr, and anything but `y` or `yes`
  sends nothing and exits 1.
- `--yes` rebuilds without asking. When stdin or stderr is not a terminal it is required: without
  it the command exits 2 and sends nothing. In a script: `osh builds rebuild --yes --watch`.
- Once the rebuild is sent, stderr names the new build. It is then shown as `osh builds show`
  shows one.
- `--watch` watches the new build as `osh builds watch` does, with that command's output and exit
  codes.
- A watch that fails leaves the build as it is: stderr names it, for `osh builds watch` to follow.
  Another `rebuild` would start a second build.
- The rebuild is sent once. When Odoo.sh does not confirm it, or the new build is not found, the
  command exits 10: look at `osh builds list` before running it again, since a second rebuild
  can start a second build.

## Reading a log

A log is untrusted text, and `osh logs` writes it to stdout only: nothing of a log goes to
stderr, with `--debug` or without.

- On a terminal the escape sequences and control characters are removed from each line, tabs
  kept, so a line cannot clear the screen or set the window's title. When stdout is a pipe or a
  file the lines are written as they are. `--strip` and `--no-strip` force one or the other.
- A line longer than 64 KiB is cut, and bytes that are not UTF-8 are replaced. A character
  stdout cannot encode is written as its escape, such as `\u2192`.
- Each line is flushed as it is printed. When the pipe it is written to closes, as under
  `osh logs | head`, the command ends with nothing on stderr and exits 141.
- `--follow` asks Odoo.sh for new lines every second, until Ctrl+C, which exits 130.
- `--kinds` lists the logs the build has: the kind, what Odoo.sh calls the log, its size and how
  long ago it was last written to. A kind `osh` does not know is `unknown`, and `--kind` takes
  its name. A build that waits for a worker has no log yet.
- A log the build does not have exits 4, and the message names the ones it has.

## A shell on a build

`osh ssh` opens a shell on the latest build of the [branch](#project-and-branch), with your own
`ssh`: its configuration, its agent and the keys registered on your Odoo.sh account. `osh` reads
no key, and its process becomes `ssh`, so the exit code is then `ssh`'s.

- The options of `osh` come first. Everything from the first argument on is given to `ssh` after
  the host: a command to run on the build, or options of `ssh`. Put `--` before it when it starts
  with a dash: `osh ssh -- -L 8069:localhost:8069`.
- A build with no address exits 4.
- Where there is no `ssh` to become, as on native Windows, the command to run by hand is shown
  and `osh` exits 14.

## Letting your address through the firewall

A project's firewall lists the IPv4 addresses that may reach its production and staging builds
by SSH, and by PostgreSQL on a dedicated server. Odoo.sh takes 10 at most, and can take up to 5
minutes to apply a change. `osh firewall list` shows them, and `osh firewall allow` changes them,
as the Firewall setting of the project's settings page does.

```bash
osh firewall allow                 # your public address becomes the only one
osh firewall allow --add           # your public address is added to the others
osh firewall allow 1.1.1.1   # this address becomes the only one
```

- Without `ADDRESS`, `osh` asks https://api.ipify.org for the public address you reach the
  internet from, and says it on stderr. It asks nothing else there, and nothing at all when an
  address is given. When none is found it exits 15.
- `ADDRESS` can also be a range in CIDR notation, such as `8.8.4.0/24`. It replaces the others
  unless `--add` is given.
- Odoo.sh takes public addresses only. One that is not a public IPv4 address or range exits 2,
  and one that would take the firewall past 10 entries exits 9, before anything is asked or sent.
  An entry Odoo.sh refuses all the same exits 9, and nothing is changed.
- It says on stderr what the firewall holds now and what it will hold, then asks as
  [`osh builds rebuild`](#rebuilding-a-branch) does, with the same `--yes`. A firewall that
  already is what it would be is not changed, and nothing is asked.
- The change is sent once, and the firewall Odoo.sh answers is shown as `osh firewall list` shows
  it. When Odoo.sh does not answer the addresses it was given, the command exits 10: look at
  `osh firewall list` before running it again.

## Scripts

### Output

`--format table` (the default) or `--format json`, given before the command. Only the result is
written to stdout, so `osh --format json ... | jq` receives nothing else.

```bash
osh --format json builds show --project acme --branch feature-x | jq -r .result
```

- JSON is the whole model of the library, and its keys are the model's attribute names in the
  [API reference](reference.md). Datetimes are ISO 8601 with an offset.

    | Command | JSON on stdout |
    | --- | --- |
    | `osh auth status` | One `SessionInfo`. |
    | `osh auth status --check` | One `Identity`, with the `SessionInfo` as its `session`. |
    | `osh projects list` | An array of `Project`. |
    | `osh branches list` | An array of `Branch`, in the order of the table. |
    | `osh builds list` | An array of `Build`. |
    | `osh builds show`, `osh builds rebuild` | One `Build`. |
    | `osh builds watch`, `osh builds rebuild --watch` | One `Build` per line, for each change, the first being the new build after a rebuild. The result line goes to stderr. |
    | `osh logs` | One `LogLine` per line. |
    | `osh logs --kinds` | An array of `Log`. |

- Three commands print an object of `osh`'s own, on one line:

    | Command | JSON on stdout |
    | --- | --- |
    | `osh auth login` | `identity`: the `Identity` of the stored session, or `null` when `ODOUCHE_SESSION` is set. |
    | `osh auth logout` | `source`: where the session came from, or `null` with none. `deleted`: whether it was removed from the keyring. `invalidated`: whether Odoo.sh ended it. `failure`: why Odoo.sh could not be asked, or `null`. |
    | `osh --version` | `osh` and `odouche`: the version of each. |

- An empty result is `[]` as JSON, and one line on stderr as a table. Both exit 0.
- A table has no colour and no box drawing when stdout is not a terminal or `NO_COLOR` is set.
  The result of a build is coloured on a terminal, and always written out.
- A table shows times as how long ago they were. JSON has the time itself.
- A stage, a status or a result `osh` does not know is shown as Odoo.sh names it.
- A table has the control characters removed from every cell, and a tab or a line break made a
  space, so a commit message cannot drive the terminal. JSON has them escaped, in a `LogLine`
  too, so `--strip` has no effect on it. The lines of [`osh logs`](#reading-a-log) keep them when
  stdout is not a terminal.

### Exit codes

A command that fails prints one or two lines on stderr, what happened and what to do, and nothing
on stdout. Three commands do otherwise:

- `osh builds watch`, and `osh builds rebuild --watch` like it, exits non-zero for a build that
  did not succeed, with the result line on stdout, or on stderr as JSON. `osh builds list` and
  `osh builds show` exit 0 whatever the build's result.
- `osh logs` can fail after lines went to stdout, at a follow's timeout (8) or when a request
  fails (7).
- `osh ssh`, once it has become `ssh`, exits with what `ssh` exits with: the code of the remote
  command, or 255 for an error of `ssh` itself. The table below is not how to read it.

| Code | Meaning | Returned by |
| --- | --- | --- |
| 0 | Success. `osh auth logout` with nothing to log out of is one. | Every command |
| 1 | Unexpected error, which is a bug to report, or a prompt or a question that was declined. | Every command |
| 2 | Usage error: an unknown option, a value an option does not take, options that do not go together, no [project or branch](#project-and-branch), an address that is not a public IPv4 address or range, or a change that cannot ask. | Every command |
| 3 | No session, or an expired one. Run `osh auth login`. | Every command that needs the session |
| 4 | The project, branch, build or log is not one the logged-in user can reach. A build that is not among the branch's latest ones is not, and neither is the build of a branch that has none. | Every command that takes a project |
| 5 | The session is not allowed to do this. | Every command that asks Odoo.sh |
| 6 | Odoo.sh answered in a shape `osh` does not read. Please report it. | Every command that asks Odoo.sh |
| 7 | Odoo.sh could not be reached, or answered with a server error. | Every command that asks Odoo.sh |
| 8 | A followed log or a login reached its timeout. | `osh logs --follow --timeout`, `osh auth login` |
| 9 | Refused, and nothing was changed: a read-only run, a branch in a stage `osh` does not change, or a firewall change refused: past the 10 entries Odoo.sh takes, or an entry it does not take. | `osh builds rebuild`, `osh firewall allow` |
| 10 | A change was sent and Odoo.sh did not confirm it. `osh builds list` shows whether the build was started, and `osh firewall list` what the firewall holds. | `osh builds rebuild`, `osh firewall allow` |
| 11 | No usable keyring: none to store the session in, or one that stayed locked. | `osh auth login`, `osh auth logout`, every command that reads the stored session |
| 12 | The login ended without a session. | `osh auth login` |
| 13 | Any other error from the library. | Every command |
| 14 | No `ssh` to hand over to, or native Windows. The command to run is shown. | `osh ssh` |
| 15 | No address was given, and the public one was not found. Give it as `ADDRESS`. | `osh firewall allow` |
| 20 | The build failed. `osh logs` is the next step. | `osh builds watch`, `osh builds rebuild --watch` |
| 21 | The build finished with warnings. | `osh builds watch`, `osh builds rebuild --watch` |
| 22 | The build ended without a result. It was dropped for a newer build, killed or skipped, or its result is one `osh` does not know. | `osh builds watch`, `osh builds rebuild --watch` |
| 23 | The build had not finished, or had not appeared, at the timeout. | `osh builds watch`, `osh builds rebuild --watch` |
| 24 to 29 | Reserved for the result of a build. | None yet |
| 130 | Interrupted with Ctrl+C. | Every command |
| 141 | Stdout is a pipe and its reader, such as `head`, closed it. Nothing is written to stderr. | Every command |

`--debug`, or `OSH_DEBUG=1`, adds the traceback on stderr. It shows no local variables, and a
session supplied through `ODOUCHE_SESSION` is masked in it.

### Environment variables

| Variable | What it does | Read by |
| --- | --- | --- |
| `ODOUCHE_SESSION` | The session to use, as the value of the `session_id` cookie of `www.odoo.sh`. It is kept in memory only, and the keyring is not read. | Every command that needs the session |
| `OSH_PROJECT` | The [project](#project-and-branch), when `--project` is not given. | Every command that takes `--project` |
| `OSH_BRANCH` | The [branch](#project-and-branch), when `--branch` is not given. | Every command that takes `--branch` |
| `OSH_DEBUG` | `OSH_DEBUG=1` is `--debug`. | Every command |
| `NO_COLOR` | Set to a value that is not empty, it leaves tables without colour and box drawing. | Every command that prints a table |
| `DISPLAY`, `WAYLAND_DISPLAY` | On Linux, with neither set, no browser is launched and the cookie is asked for. | `osh auth login` |
| `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY` | The proxy that requests to Odoo.sh are sent through, and the hosts that are reached without it. The lowercase names are read too. `HTTP_PROXY` has no effect: every request is HTTPS. | Every command that asks Odoo.sh |
| `SSL_CERT_FILE`, `SSL_CERT_DIR` | The certificate authorities trusted for those requests, as a file or a directory, in place of the system's. The file wins over the directory. | Every command that asks Odoo.sh |

- With `ODOUCHE_SESSION` set, `osh auth login` still stores the session it gets in the keyring,
  and `osh auth logout` leaves the variable for you to unset.
- Git, which reads the checkout, and Rich, which draws the tables and the help, read variables of
  their own, such as `GIT_DIR` and `TERM`.

## Shell completion

```bash
osh --install-completion
```

- It installs completion for the shell it is run from, such as bash, zsh or fish: a script under
  the home directory, and for bash and zsh a line in `~/.bashrc` or `~/.zshrc`. It takes effect in
  a new shell.
- `osh --show-completion` prints the script instead, to install it yourself.
- Commands, options and the values an option lists are completed. Names of projects and branches
  are not: completing asks Odoo.sh nothing and does not open the keyring.
- It needs `osh` on the `PATH`, so it does not go with `uvx`.
