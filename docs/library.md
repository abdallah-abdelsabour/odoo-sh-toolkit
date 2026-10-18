# Library

`odouche` is the Python client for Odoo.sh that the command-line tool and the MCP server are built
on. It is meant to be used directly in your own tools as well.

```bash
uv add odouche
```

## What it is designed to be

- **Typed.** The library returns typed models rather than raw Odoo.sh payloads, and ships type
  information (`py.typed`).
- **The only layer that knows Odoo.sh.** Endpoints, payload shapes and anything scraped from the
  web interface live in one place, so an upstream change is fixed in one place.
- **Synchronous.** Every call is a plain function. From async code, run it in a thread, for
  example with `asyncio.to_thread`.
- **Streaming where it matters.** Watching a build and following logs return iterators of typed
  events. Close the iterator to stop; pass a deadline to bound it.

## Session

The session is taken from the `ODOUCHE_SESSION` environment variable when it is set, and from the
OS keyring otherwise. [Security](security.md) says where it is stored and for how long.

For headless use, such as CI, set `ODOUCHE_SESSION` to the `session_id` cookie of `www.odoo.sh`
from the job's secrets. The keyring is then neither read nor written, and nothing below changes.

## Logging in

`login` opens a browser for the user to sign in to Odoo.sh with GitHub, then stores the session in
the keyring, replacing the one stored before.

```python
import getpass

import odouche


def ask() -> odouche.Secret:
    return odouche.Secret(getpass.getpass("session_id cookie of www.odoo.sh: "))


odouche.login(ask=ask, notify=lambda step: print(step.value), timeout=300)
```

- The library neither prints nor prompts. `notify` is called with a `LoginStep`: `KEYRING` when
  the keyring is checked and may be showing a dialog to be unlocked, `BROWSER` when the window is
  open, `PASTE` when no browser can be launched and `ask` is about to be called.
- `ask` returns the pasted cookie, wrapped in a `Secret`. Without it, a machine with no browser to
  launch gets a `LoginError`.
- The session is stored once Odoo.sh has answered one request sent with it. A login that is
  refused or times out leaves the stored session as it was.
- A login nobody completes raises `LoginTimeoutError` after `timeout` seconds.
- Called from the main thread, a login that `SIGTERM` or `SIGHUP` ends deletes the browser profile
  before the signal takes effect.
- With no keyring to store the session in, or one not unlocked within `timeout` seconds,
  `KeyringUnavailableError` is raised before the browser opens.
- Outside a login, a keyring showing a dialog is waited for ten seconds, then
  `KeyringUnavailableError` is raised.

## Logging out

`logout` ends the stored session on Odoo.sh, so that a copy of it stops working, and deletes it
from the keyring.

```python
import odouche


result = odouche.logout()
if result.failure is not None:
    print("Deleted here, but Odoo.sh could not be asked to end it:", result.failure)
```

It returns a `LogoutResult`:

| Field | Meaning |
| --- | --- |
| `source` | A `SessionSource`: `KEYRING`, `ENVIRONMENT` or `ARGUMENT`. `None` when there was no session. |
| `deleted` | Whether the stored session was deleted. |
| `invalidated` | Whether Odoo.sh has ended the session. |
| `failure` | The error that kept Odoo.sh from being asked, or `None`. |

- The stored session is deleted even when Odoo.sh cannot be reached. `failure` then holds the
  error, and the session works until Odoo.sh expires it.
- With no session, nothing is asked and nothing is raised. A stored one past its max age, or
  that cannot be read, is deleted and not ended on Odoo.sh.
- A session from the environment or passed as `logout(session)` is not deleted: unset it
  yourself. It is ended on Odoo.sh only with `invalidate_given=True`, since a session shared
  between jobs would stop working for all of them. The stored session is then left as it is:
  unset the variable and call `logout()` again to end it.
- A session passed or set in the environment that is not a `session_id` cookie value raises
  `NoSessionError`.
- `KeyringUnavailableError` is raised when the keyring cannot be read or the session not deleted.
  When it is not deleted, the message says whether Odoo.sh ended it.

## Client

`Client` is the one object everything is asked through. It closes its connections when the block
ends.

```python
import odouche


with odouche.Client() as client:
    for project in client.projects():
        print(project.name, project.repository, project.url)
```

- With no argument, the session is resolved as [above](#session). A tool that keeps sessions itself
  passes its own, `odouche.Client(odouche.Secret(value))`, which is never stored.
- With no session, or a value that is not a `session_id` cookie value, `Client()` raises
  `NoSessionError`.
- Nothing is cached: each call asks Odoo.sh.

### Who is logged in

`client.identity()` asks Odoo.sh who the session belongs to, and returns an `Identity`. Being
answered also means the session is still valid: a rejected one raises `SessionExpiredError`.

| Field | Meaning |
| --- | --- |
| `user_id` | The number Odoo.sh gives the user. |
| `name` | The user's display name, or `None`. |
| `username` | The user's GitHub login. |
| `email` | The user's email address, or `None`. Treat it as personal data. |
| `session` | The `SessionInfo` below. |

`client.session` is a `SessionInfo`, read without asking Odoo.sh, so it works offline. It never
holds the session itself.

| Field | Meaning |
| --- | --- |
| `source` | A `SessionSource`: `KEYRING`, `ENVIRONMENT` or `ARGUMENT`. |
| `stored_at` | When the login stored the session. `None` unless it came from the keyring. |
| `expires_at` | When it passes the client-side max age. `None` unless it came from the keyring. |

### Projects

`client.projects()` returns the projects the session's user can reach, as a list of `Project`:

| Field | Meaning |
| --- | --- |
| `id` | The number Odoo.sh gives the project. |
| `name` | The project's name on Odoo.sh, as in the address of its page. |
| `repository` | The GitHub repository the project builds, as `owner/name`. |
| `url` | The address of the project's page on Odoo.sh. |

### Branches

`client.branches(project)` returns the branches of a project, as a list of `Branch`. `project` is a
`Project` or a project's name.

| Field | Meaning |
| --- | --- |
| `id` | The number Odoo.sh gives the branch. |
| `name` | The git branch. |
| `stage` | A `Stage`: `PRODUCTION`, `STAGING`, `DEVELOPMENT` or `UNKNOWN`. |
| `stage_name` | What Odoo.sh calls that stage, such as `dev`. |

- The branches come in the order Odoo.sh answers them, which is not by name or by stage.
- A stage the library does not know is `Stage.UNKNOWN`, not an error. `stage_name` says which.
- A project that is not among those the user can reach raises `NotFoundError`, whether or not it
  exists. One that Odoo.sh lists but refuses the branches of raises `PermissionDeniedError`.
- Each call asks Odoo.sh twice: for the projects, then for the branches.

### Builds

`client.builds(branch)` returns the latest builds of a branch, newest first, as a list of `Build`.
`branch` is a `Branch` or a branch's number.

```python
import odouche


with odouche.Client() as client:
    branches = {branch.name: branch for branch in client.branches("acme-shop")}
    branch = branches["feature-invoicing"]
    for build in client.builds(branch, limit=2):
        print(build.id, build.status, build.result, build.commit.hash)
    latest = client.latest_build(branch)
```

| Field | Meaning |
| --- | --- |
| `id` | The number Odoo.sh gives the build. |
| `name` | The build's name on Odoo.sh. |
| `branch_id`, `branch_name` | The branch the build belongs to. |
| `commit` | A `Commit`: `hash`, `message`, `author`, `timestamp` and `url`. |
| `status` | A `BuildStatus`: `PROGRESS`, `UPDATING`, `DONE`, `DROPPED`, `SKIPPED`, `KILLED` or `UNKNOWN`. |
| `status_name` | What Odoo.sh calls that status. |
| `result` | A `BuildResult`: `SUCCESS`, `FAILED`, `WARNING` or `UNKNOWN`. `None` when Odoo.sh gives none. |
| `result_name` | What Odoo.sh calls that result. `None` with `result`. |
| `status_info` | What the build is doing, in Odoo.sh's own words, or `None`. |
| `started_at` | When a worker took the build. `None` while it waits for one. |
| `url` | The address of the build's database, or `None`. |
| `finished` | Whether the build has ended, whatever its result. |

- `finished` is true for `DONE`, `DROPPED`, `SKIPPED` and `KILLED`. A `DROPPED` build is one a
  newer build replaced.
- A status or result the library does not know is `UNKNOWN`, not an error, and the `_name` field
  says which. An unknown status is not finished.
- Timestamps are timezone-aware, in UTC.
- `limit` defaults to 4 and each call is one request. Odoo.sh may return fewer builds than asked
  for: it has only been seen to return up to four, and older builds are out of reach. A `limit`
  under 1 raises `ValueError`, and a branch, build or limit that is not an integer `TypeError`.
- `client.latest_build(branch)` returns the newest build, or `None` for a branch with none.
- `client.build(branch, build_id)` returns one build. Odoo.sh has no request for a build by its
  number, so it is looked for among the branch's latest and raises `NotFoundError` when it is not
  there.
- A branch the user cannot reach raises `NotFoundError`. Odoo.sh gives no reason.
- A commit's `author` is another person's name. Treat it as personal data.

`client.watch_build(project, build, timeout=...)` yields the build as it is now, then again at
each change, and ends once it has finished. `project` is a `Project` or a project's name.

```python
import odouche


with odouche.Client() as client:
    branches = {branch.name: branch for branch in client.branches("acme-shop")}
    branch = branches["feature-invoicing"]
    build = client.latest_build(branch)
    if build is None:
        raise SystemExit("The branch has no build yet.")
    try:
        for build in client.watch_build("acme-shop", build, timeout=1800):
            print(build.status, build.status_info)
    except odouche.StreamTimeoutError:
        print("Still running after half an hour.")
    else:
        print(build.result)
```

- A change is one of `status`, `result` or `status_info`. The last build yielded is the finished
  one, and a build that has already finished is yielded once, with no wait.
- The watch stays on its build. A newer build on the branch ends it as `DROPPED`.
- With `pulse=2`, the build is also yielded unchanged, every 2 seconds at least while nothing
  changes, so a caller that watches in a thread can close the iterator.
- After `timeout` seconds it raises `StreamTimeoutError`: the build is still running, which is not
  Odoo.sh being unavailable. A request of the watch waits no longer than what is left of them.
- Changes come over a websocket to `www.odoo.sh`. Odoo.sh is also asked for the build at the
  start, when the connection is opened again and after a minute with no news of the build, which
  is what detects a rejected session.
- A connection that fails is opened again twice. Then `UpstreamUnavailableError` is raised.
- Closing the iterator closes the connection.
- A build that is no longer among its branch's latest raises `NotFoundError`.

### Logs

`client.logs(project, build)` returns the logs a build has, as a list of `Log`. `project` is a
`Project` or a project's name, and `build` a `Build`.

| Field | Meaning |
| --- | --- |
| `kind` | A `LogKind`: `INSTALL`, `PIP`, `ODOO`, `UPDATE`, `NEUTRALIZE`, `UPGRADE` or `UNKNOWN`. |
| `name` | What Odoo.sh calls the log. |
| `modified_at` | When it was last written to, in UTC. |
| `size` | Its size in Odoo.sh's own words, such as `156 KB`. |

`client.read_log(project, build, kind)` yields the lines of one log, and
`client.follow_log(project, build, kind, timeout=...)` the lines written from now on. `kind` is a
`LogKind` or a log's name.

```python
import odouche


with odouche.Client() as client:
    branches = {branch.name: branch for branch in client.branches("acme-shop")}
    branch = branches["feature-invoicing"]
    build = client.latest_build(branch)
    if build is None:
        raise SystemExit("The branch has no build yet.")
    for log in client.logs("acme-shop", build):
        print(log.name, log.size)
    for line in client.read_log("acme-shop", build, odouche.LogKind.INSTALL, tail=20):
        print(repr(line.text))
    try:
        for line in client.follow_log("acme-shop", build, odouche.LogKind.ODOO, timeout=600):
            print(repr(line.text))
    except odouche.StreamTimeoutError:
        print("Followed for ten minutes.")
```

Each line is a `LogLine`:

| Field | Meaning |
| --- | --- |
| `text` | The line without its newline. |
| `offset` | The byte just past the line. |
| `truncated` | Whether the line was cut. |

!!! warning "Log content is untrusted"

    A line is whatever a process printed, unchanged: it can hold terminal escape sequences and
    secrets of the instance. Neutralise it before showing it.

- A build has only some of the logs, and none while it waits for a worker. A log it does not have
  raises `NotFoundError`.
- `tail=N` yields the last N lines, out of the log's last mebibyte.
- Closing the iterator of a read closes the connection.
- A line longer than 64 KiB is cut to that and marked `truncated`. Bytes that are not UTF-8 are
  replaced.
- `follow_log` starts after the last `tail` lines, none by default, or at `offset`: pass the
  `offset` of the last line read to carry on from it.
- A follow asks Odoo.sh every second and ends when the iterator is closed. After `timeout` seconds
  it raises `StreamTimeoutError`. `timeout=math.inf` sets no limit.
- A request that fails is sent again twice, from where the last one stopped, so no line is lost
  or repeated. Then `UpstreamUnavailableError` is raised.
- Each call asks Odoo.sh for the build's worker and the project's access token before the log.
  [Security](security.md#build-logs) says what happens to the token.

### A shell on a build

`client.ssh_target(build)` returns the `SshTarget` of a build: the `user` and the `host` that
`ssh` reaches it with. Odoo.sh is not asked, and the library opens no connection and reads no key.

```python
import odouche


with odouche.Client() as client:
    build = client.build(51044, 88212)
    target = client.ssh_target(build)
print("ssh", "-l", target.user, target.host)
```

- Both values are checked to be safe as arguments of `ssh`: one that is not raises
  `UpstreamChangedError`.
- A build with no address raises `NotFoundError`.

## State-changing operations

Every other call of the client only reads. These are the calls that change something on Odoo.sh:

| Call | What it changes |
| --- | --- |
| `client.rebuild(branch)` | Starts a new build of the branch, which replaces its latest one. |
| `client.allow_ip(project, address)` | Sets the IPv4 addresses the project's firewall lets reach its production and staging builds. |

```python
import odouche


with odouche.Client() as client:
    branches = {branch.name: branch for branch in client.branches("acme-shop")}
    branch = branches["feature-invoicing"]
    build = client.rebuild(branch)
    for build in client.watch_build("acme-shop", build, timeout=1800):
        print(build.status, build.status_info)
```

- `odouche.Client(read_only=True)` refuses them: the call raises `ReadOnlyError` and nothing is
  sent, not even the reads the call starts with. `client.read_only` tells which a client is.
- The request is sent once and never repeated. When it left and Odoo.sh did not confirm it, or
  the new build cannot be found, the call raises `OutcomeUnknownError`: look at the branch before
  calling again, since a second call can start a second build.
- `rebuild` takes a `Branch` or a branch's number and returns the new `Build`. It asks Odoo.sh
  three times: for the branch's latest build, for the rebuild, then for the new build.
- Only a development or a staging branch is rebuilt. Any other stage raises `StageRefusedError`
  before the rebuild is sent, and before any request when a `Branch` in that stage is passed.
  A rebuild has only been observed on a development branch.
- `client.check_rebuild(branch)` raises the same `StageRefusedError` for a `Branch` and asks
  nothing, on a read-only client too: call it before asking the user.
- The library asks for no confirmation. That is the caller's to do.

### Firewall

A project's firewall lists the IPv4 addresses that may reach its production and staging builds by
SSH, and by PostgreSQL on a dedicated server. `client.firewall(project)` reads it, and
`client.allow_ip(project, address)` changes it, as the Firewall setting of the project's settings
page does.

```python
import odouche


with odouche.Client() as client:
    current = client.firewall("acme-shop")
    wanted = client.plan_allow_ip(current, "9.9.9.9", replace=False)
    if wanted != current:
        current = client.allow_ip("acme-shop", "9.9.9.9", replace=False)
    print(", ".join(current.addresses))
```

- `allow_ip` makes the address the only one, or with `replace=False` adds it to the others. A
  firewall that already is what it would be is not changed, and nothing is sent.
- An address can also be a range in CIDR notation, such as `8.8.4.0/24`. Odoo.sh takes public
  ones only: anything else, a private or reserved address included, raises `ValueError` before
  anything is sent.
- `client.plan_allow_ip(firewall, address)` returns the `Firewall` that `allow_ip` would leave,
  and asks nothing: show it to the user before asking them. One that would hold more than the 10
  entries Odoo.sh takes raises `FirewallRefusedError` before anything is sent, and so does an
  entry Odoo.sh refuses all the same, which changes nothing.
- `allow_ip` asks Odoo.sh three times: for the projects, the firewall, then the change. The change
  sends the addresses setting and nothing else. When Odoo.sh does not answer the addresses it was
  given, the call raises `OutcomeUnknownError`: read the firewall before calling again.
- Odoo.sh can take up to 5 minutes to apply a change. The library does not look up your public
  address: pass the one to allow.

## Errors

Everything the library raises is an `OdoucheError`, so one `except` catches any failure and no
HTTP client exception has to be imported.

```python
import odouche


try:
    with odouche.Client() as client:
        names = [project.name for project in client.projects()]
except (odouche.NoSessionError, odouche.SessionExpiredError):
    raise SystemExit("Log in to Odoo.sh first.") from None
except odouche.OdoucheError as error:
    raise SystemExit(str(error)) from None
print(names)
```

| Error | Meaning |
| --- | --- |
| `NoSessionError` | There is no session. Log in. |
| `SessionExpiredError` | Odoo.sh rejected the session, or it passed its max age. Log in again. |
| `NotFoundError` | The project, branch, build or log is not one the session's user can reach. |
| `PermissionDeniedError` | The session is not allowed to do this. |
| `UpstreamChangedError` | Odoo.sh answered in a shape the library does not read. Please report it. |
| `UpstreamUnavailableError` | Odoo.sh could not be reached, or answered with a server error. |
| `ReadOnlyError` | A read-only client was asked to change state. Nothing was sent. |
| `StageRefusedError` | The branch is in a stage the library does not change. |
| `FirewallRefusedError` | A firewall change was refused: more than 10 entries, or one Odoo.sh does not take. Nothing was changed. |
| `OutcomeUnknownError` | A state-changing request was sent and Odoo.sh did not confirm it. Look before trying again. |
| `StreamTimeoutError` | A build was still being watched, or a log followed, at its timeout. |
| `KeyringUnavailableError` | No accepted keyring backend is available to store the session. |
| `LoginError` | The login ended without a session, or the pasted one was refused. |
| `LoginTimeoutError` | Nobody completed the browser login before its timeout. |

An error carries `operation`, `status` and a message. It never carries a session value, request
headers or a response body.

## Stability

- **The public API is what `odouche` exports**: the names in `odouche.__all__`, which the
  [API reference](reference.md) lists one by one. A module or a name with a leading underscore,
  `odouche._upstream` included, is not, and changes without notice.
- **Below 1.0, a minor release can break the public API** and a patch release does not. The
  [changelog](https://github.com/avanserv/odouche/blob/main/CHANGELOG.md) marks each breaking
  change.
- **Odoo.sh can break the library in any release**, since it has no public API. A caller sees
  that as `UpstreamChangedError`, whose `operation` and `field` name the request and what could
  not be read in its answer. No retry helps, and the input is not at fault.
- **Report one** on the [issue tracker](https://github.com/avanserv/odouche/issues) with the
  error's message, which holds no session value. Leave out the answer itself: it holds your
  projects' data.
