# Upstream reference

What Odoo.sh was observed to do, from a browser capture and from plain requests. Odoo.sh is
undocumented and can change without notice, so every section is dated. Values are scrubbed:
`session_id=REDACTED` stands for a session, and project names and logins are invented.

## Login and session

Captured on 2026-10-01.

### Login flow

Signing in with a GitHub account that has already authorised Odoo.sh:

| Hop | Request | Answer |
| --- | --- | --- |
| 1 | `GET www.odoo.sh/web/login` | 302 to `/oauth/signin` |
| 2 | `GET www.odoo.sh/oauth/signin` | 302 to `github.com/login/oauth/authorize`, with `client_id` and `scope=read:user user:email` |
| 3 | `GET github.com/login/oauth/authorize` | 302 to `www.odoo.sh/oauth/callback`, with `code` and `iss` |
| 4 | `GET www.odoo.sh/oauth/callback` | 303 to `/project`, and sets the authenticated `session_id` |
| 5 | `GET www.odoo.sh/project` | 200, the project list page |

- The authorisation request carries no `state` parameter.
- Hop 3 shows no page when GitHub is already signed in. A sign-in on GitHub itself was not
  captured.
- Odoo.sh has no login form of its own: `/web/login` only redirects.

### Session cookie

The session is the `session_id` cookie on `www.odoo.sh`. Sent alone, it authenticates a request:
no other cookie or header is needed.

| Attribute | Value |
| --- | --- |
| `Domain` | not set, so the cookie is bound to `www.odoo.sh` only |
| `Path` | `/` |
| `HttpOnly` | yes: page script cannot read it |
| `Secure` | not set |
| `SameSite` | not set |
| `Max-Age` | `691200` (8 days) |
| `Expires` | one year ahead, which `Max-Age` overrides |

- The value is 84 characters from the URL-safe alphabet.
- An anonymous `session_id` exists before login: `/web/login` and `/oauth/signin` set one on a
  request that has none. The presence of the cookie says nothing about being signed in.
- Hop 4 replaces the anonymous value with a new one.
- Page responses send the cookie again with the same value and a fresh `Max-Age`.

Other cookies on `www.odoo.sh` are `frontend_lang`, `tz` and two analytics cookies. None of them
takes part in authentication.

### Without a valid session

The answers are the same with no cookie and with a made-up `session_id`.

| Request | Status | Answer |
| --- | --- | --- |
| Page: `GET /project` | 303 | `Location: /web/login?redirect=%2Fproject%3F` |
| JSON: `POST /app/projects` | 200 | A JSON-RPC error, below |

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": 0,
    "message": "Odoo Server Error",
    "data": {
      "name": "odoo.http.SessionExpiredException",
      "message": "Session expired"
    }
  }
}
```

A JSON request never answers 401 or 403 for a missing session. The status is 200 and the only
signal is `error.data.name`.

### What else the session is tied to

- JSON requests are `POST` with `Content-Type: application/json` and a JSON-RPC body
  (`{"jsonrpc": "2.0", "method": "call", "params": {}, "id": 1}`). The read requests captured
  send no CSRF token, and are accepted without `Origin` or `Referer`.
- No browser `User-Agent` is needed. On 2026-10-02, `POST /app/projects` sent with
  `User-Agent: odouche/<version>` and a session fresh from a sign-in was answered 200 with no
  `error`. Seen once.
- The `/project` page embeds a `csrf_token`. The [rebuild](#rebuild) does not need it. Whether
  another state-changing request does is unknown.
- `POST /app/project/<project>/get_info` returns an `access_token` of 32 characters, distinct
  from the session. It is what the worker hosts take: see [Logs](#logs).

### Logout

`GET www.odoo.sh/web/session/logout?redirect=...` answers 303 to `/` and sets a new anonymous
`session_id`. It takes no CSRF token. The previous value is invalidated on the server: replayed
afterwards, it gets the `SessionExpiredException` answer above.

### User profile

Captured on 2026-10-04. Fixture: `user_profile.json`.

`POST /app/user/profile`, no params, is what the profile page loads. `result` is the signed-in
user:

| Field | Type | Observed |
| --- | --- | --- |
| `id` | int | The `hosting_user_id` of [Projects](#projects) |
| `name` | str | The display name |
| `username` | str | The GitHub login |
| `email` | str | |
| `avatar_url`, `url` | str | The GitHub avatar and profile |
| `notification_push`, `notification_mail` | str | `off`, `warning` |
| `changelog` | bool | |
| `ssh_keys` | list | `id`, `fingerprint`, `name` |
| `devices` | list | `id`, `name`, `type`, `last_activity`, `ip`, `is_current`, `location` |

- It needs no project, unlike the `user` of `get_info`.
- `devices` look like the user's sessions, each with an address and a location. The library reads
  neither list, and the fixture leaves them out.
- Seen once. What `name` and `email` are for a user who has none is unknown: the library reads
  `false` and `null` as none.

### Hosts

Contacted during login and on the project pages:

| Host | Role | Authentication |
| --- | --- | --- |
| `www.odoo.sh` | Pages, every `/app/...` JSON request and the bus websocket | `session_id` |
| `<worker>.odoo.com` | Build logs, under `/paas/...` | The project's `access_token` |
| `github.com` | Hop 3 of the login | GitHub's own session |
| `avatars.githubusercontent.com` | User avatars | None |

The pages also load fonts, analytics and an embedded video from third parties. A client needs
none of them.

Each build has a host of its own, `<build name>.dev.odoo.com`, which serves
its database, its shell (`/odoo-sh/webshell/ws`) and its editor (`/odoo-sh/editor/lab`). The
`www.odoo.sh` session is not sent there, since the cookie is bound to `www.odoo.sh`. Without
authentication of its own, the build host answers 302 to
`www.odoo.sh/paas/build/<build id>/token?redirect=...&otp=0`, so access is handed over from the
`www.odoo.sh` session. The hops after that were not captured.

A build is reached over SSH as `<build id>@<host of the build's url>`, which is what the page
shows for a development, a staging and the production build (seen in the page on 2026-10-06, not
captured: no payload has it). The keys are those registered on the user's account. The session has no part in it.

Named in the project payloads but not contacted:

| Host | Role |
| --- | --- |
| `<project>.odoo.com` | The production database |
| `upgrade.odoo.com` | Odoo's upgrade service |

### Unknown

- How long a session lasts on the server, and whether use extends it. The cookie declares 8 days
  and is renewed by page responses.
- What the `/paas/build/<build id>/token` handover gives the build host, and how long it lasts.
- Whether state-changing requests other than the rebuild need the `csrf_token`.

## Projects, branches, builds and logs

Captured on 2026-10-01, on one project with a production, a staging and a development branch.
The project list held two projects. Where the capture could not show something, the page's own
script was read, and the text says so.

The fixtures are in `packages/odouche/tests/fixtures/`, written from the shapes below with
invented values. Lists the library does not read are left out of `project_info.json`.

### Common shape

Every `/app/...` request is `POST www.odoo.sh` with `Content-Type: application/json`, the
`session_id` cookie and this body, where only `params` varies:

```json
{"jsonrpc": "2.0", "method": "call", "params": {}, "id": 1}
```

The answer is 200 with `{"jsonrpc": "2.0", "id": 1, "result": ...}`.

- An absent value is `false`, not `null`: a field typed as a string below is "string or `false`".
  The library reads `null` as absent too.
- Timestamps are `YYYY-MM-DD HH:MM:SS` in UTC with no zone marker. Dates are `YYYY-MM-DD`.
- A many-to-one is a pair `[id, name]`.

### Identifiers

| Thing | Identified by | Where |
| --- | --- | --- |
| Project, on its page and in `get_info` | `project_name` | `/project/<project_name>`, `/app/project/<project_name>/get_info` |
| Project, in every other project request | `technical_name` | `/app/project/<technical_name>/branches` |
| Branch | numeric `id` | `/app/branch/<id>/...` |
| Build | numeric `id` | `/app/build/<id>/...`, `<worker_url>/paas/build/<id>/...` |

- `technical_name` is `<owner>-<name>-<id>` in lower case, from the project's GitHub owner and
  repository name and its numeric id.
- A project's `url` is its GitHub clone address, `https://github.com/<owner>/<name>.git`, with
  the case GitHub has.
- A branch's `name` is the git branch name. Its `provider_url` is
  `https://github.com/<owner>/<name>/tree/<branch>`.
- A build's `name` is not derivable: development and staging builds were named
  `<project_name>-<branch>-<id>`, production branch builds `<owner>-<name>-<branch>-<id>`, with
  dots turned into dashes. Take `name` and `url` from the payload.
- A build's `url` is `https://<name>.dev.odoo.com`, or `https://<project_name>.odoo.com` for the
  production build.

### Projects

`POST /app/projects`, no params. Fixture: `projects.json`.

`result.repos` is the list, with no pagination seen. Per project:

| Field | Type | Observed |
| --- | --- | --- |
| `id` | int | |
| `project_name` | str | Lower case |
| `technical_name` | str | |
| `owner`, `name` | str | The GitHub owner and repository |
| `url` | str | The clone address |
| `project_url` | str | `https://www.odoo.sh/project/<project_name>` |
| `odoo_branch` | str | `19.0` |
| `production_build_id` | `[id, name]` | |
| `geolocation` | str | `Europe` |
| `subscription_validity` | str | `valid` |
| `user_is_admin` | bool | `true` |
| `collaborators` | list of str | GitHub logins |

`result.hosting_user_id` is the signed-in user's id.

`POST /app/project/<project_name>/get_info`, no params, is what a project page loads first.
Fixture: `project_info.json`. It returns the `access_token`, the `user` (`id`, `username`,
`email`, `access`), `active_repo`, the other `repos`, and `mappings`, which holds the
[enumerations](#enumerations).

Seen once each. `projects` held two projects; `get_info` was called for one.

### Branches

`POST /app/project/<technical_name>/branches`, no params. Fixture: `branches.json`.

`result` is the list of every branch, with no pagination seen and no order by id or stage.

| Field | Type | Observed |
| --- | --- | --- |
| `id` | int | |
| `name` | str | The git branch |
| `slug` | str | `<name>-<id>`, dots as dashes |
| `stage` | str | `production`, `staging`, `dev` |
| `provider_url` | str | |
| `last_build_id` | `[id, name]` | |
| `last_build_status` | str | `done`, `dropped`, `progress` |
| `last_build_result` | str or `false` | `success`; `false` while in progress |
| `push_behavior` | str | `new`, `update` |

`POST /app/project/<technical_name>/builds_per_branch`, no params, answers the same list as
`[{"branch_info": <branch>, "builds": [...]}]` with the last two to four builds of each branch.

Seen four times on one project, across the three stages.

### Builds of a branch

`POST /app/branch/<id>/builds`, params `{"build_limit": 4}`. Fixture: `builds.json`.

`result` is a list of one `{"branch_info": <branch>, "builds": [...]}`. `builds` holds at most
`build_limit` builds. On the development branch they came newest first.

- `build_limit` was sent as 1, 2 and 4. Without it, four builds came back. Whether a larger
  value is honoured is unknown.
- There is no offset. Older builds are only reachable through the history.

| Field | Type | Observed |
| --- | --- | --- |
| `id` | int | |
| `name` | str | |
| `branch_id` | `[id, name]` | |
| `stage` | str | `dev`, `staging`, `production`, `dummy` |
| `status` | str | `progress`, `done`, `dropped` |
| `result` | str or `false` | `success`, `failed`; `false` while in progress |
| `status_info` | str or `false` | See [Enumerations](#enumerations) |
| `start_datetime` | str or `false` | `false` until a worker takes the build |
| `run_time` | str or `false` | `H:MM:SS`; `false` while in progress |
| `url` | str | |
| `worker_url` | str or `false` | `https://<worker>.odoo.com`; `false` until a worker takes the build |
| `expiration_date` | str or `false` | A date, on a staging build |
| `head_commit_author`, `head_commit_msg` | str | |
| `head_commit_timestamp`, `head_commit_url` | str | |

A branch keeps the stage it has now; its builds keep the stage they were built in. The staging
branch listed `dummy` builds and the production branch a `dev` one.

`POST /app/branch/<id>/history`, params `{"offset": 0}`. Fixture: `history.json`.

`result` is `{"num_trackings": <total>, "trackings": [...]}`, newest first. A tracking is one
event on the branch:

| Field | Type | Observed |
| --- | --- | --- |
| `id` | int | |
| `tracking_type` | str | `push`, `rebuild`, `stage` |
| `create_date` | str | |
| `build` | object or `false` | `false` on the `stage` tracking that created the branch |
| `commits` | list | `identifier`, `message`, `provider_url`; up to 28 in one push |
| `pusher_name`, `pusher_url`, `pusher_avatar_url` | str or `false` | |
| `source_stage`, `target_stage` | str or `false` | Set on `stage` trackings |

Inside a tracking, `build.status` and `build.result` are pairs `[value, label]`, such as
`["dropped", "Dropped"]`, unlike in the builds answer.

- Only `offset` 0 was captured, and no branch had more than 13 trackings. The page's script pages
  by 20; that an `offset` of 20 returns the next page is inferred.

`builds` was seen 19 times and `history` 13, on one project across the three stages.

### One build

No request reads a build by its id. The page takes a build from the branch's `builds` answer:
`{"build_limit": 1}` returns the latest one. Fixtures: `build_progress.json`, `build_done.json`.

A build that is not among a branch's last four is only in the history, in the shorter form above.

`POST /app/build/<id>/errors`, no params, answered an empty list for a successful build. Seen
once.

### Build status changes

The page does not poll: over a rebuild it sent no request to `www.odoo.sh`. Changes are pushed
over Odoo's bus, a websocket opened from a shared worker, which is why a capture of the page does
not show it. Fixture: `build_events.json`.

A client outside a browser can open the socket and receives the same events, with the
`session_id` cookie and an `Origin` header.

`GET wss://www.odoo.sh/websocket?version=<version>` answers 101.

- `Origin: https://www.odoo.sh` is required. Without it the answer is 400, with or without a
  session.
- The session is the `session_id` cookie, as on a JSON request.
- `version` is the `v` of the worker's script, `/bus/websocket_worker_bundle?v=18.0-7`. With a
  made-up version the socket opened and sent nothing for 15 seconds, in which no build ran.
- No subprotocol is asked for. The browser offers `permessage-deflate` and the answer does not
  take it.
- With no session, or a made-up one, the answer is still 101 and the subscription is accepted.
  No frame follows. The socket gives no sign that a session is missing.

The client sends text frames. To subscribe:

```json
{"event_name": "subscribe", "data": {"channels": ["paas_repository:4217"], "last": 0}}
```

- The channel is `paas_repository:<project id>`, with the project's numeric `id`.
- `last` is the id of the last notification the client holds. The page sends the latest one it
  has; `0` was accepted.
- The page subscribes once with no channel on connecting, then again with the project's. It also
  sends an `update_presence` message. Events arrived without either.
- Idle, the page sends a binary frame of one zero byte, 60 seconds after the last frame and every
  60 seconds from then.

The server sends text frames, each a list of notifications:

```json
[{"id": 700390, "message": {"type": "paas.repository/build_event", "payload": {}}}]
```

- `id` increases from one notification to the next. One frame can hold several.
- A `paas.repository/build_event` payload is `repository_id` (the project's `id`), `branch_id`,
  `build_id` and `values`.
- `values` is either the whole build, with the fields of the `builds` answer, or a short form of
  `id`, `status`, `result` and sometimes `status_info`. The short form was the first two events
  of a new build.
- A `paas.repository/new_tracking` payload is `repository_id`, `branch_id` and `build_id`. It came
  in the same frame as the new build's first event.
- The channel is the project's, not the build's. Events came for builds of the staging and the
  production branch, unchanged, and for the branch's previous build as it became `dropped`.
- Other types arrive, such as `bus.bus/im_status_updated`.

Over a rebuild, the new build's events were:

| `status` | `result` | `status_info` | `values` |
| --- | --- | --- | --- |
| `progress` | `false` | absent | Short |
| `progress` | `false` | `Starting build...` | Short |
| `progress` | `false` | `Installing dependencies...` or `Installing database...` | Whole |
| `progress` | `false` | `Installing: <module>` or `Testing: <module>` | Whole, 6 to 16 seconds apart |
| `done` | `success` | `done` | Whole |

Seen on two rebuilds of one development branch, once from the worker and once from outside a
browser. Nothing closed the socket in 11 minutes with the idle frame, or in 4 minutes without it.

Without the socket, a client can only ask `builds` again. Upstream sets no interval for that,
since the page never does it. Successive `builds` answers for the rebuilt branch showed:

| `status` | `result` | `status_info` | `start_datetime`, `worker_url` | `run_time` |
| --- | --- | --- | --- | --- |
| `progress` | `false` | `false` | `false` | `false` |
| `progress` | `false` | `Installing: <module>` | set | `false` |
| `done` | `success` | `done` | set | set |

### Logs

Logs are not on `www.odoo.sh`. They are on the build's `worker_url`, authenticated by the
project's `access_token` and not by the session. The same token opened the logs of every build
seen: it is a secret, and it travels in a URL.

`POST <worker_url>/paas/build/<id>/logs/list`, a JSON-RPC body with params
`{"token": "<access_token>"}`. Fixture: `build_logs_list.json`.

`result` is a list of `{"name", "write_date", "size"}`, with `size` as text such as `156 KB`.

`GET <worker_url>/paas/build/<id>/logs/<name>?token=<access_token>` with a `Range` header.
Fixture: `build_log_install.txt`.

| Request | Answer |
| --- | --- |
| `Range: bytes=-1048576` | 206, the last mebibyte, with `Content-Range: bytes <first>-<last>/<size>` |
| `Range: bytes=<last>-` | 206, from the last byte already held: one byte when nothing is new |
| Either, on an empty log | 200, no body and no `Content-Range` |

- The body is `text/plain; charset=utf-8`, in Odoo's log format for `odoo`, `install` and
  `update`.
- Following is polling: the page repeats the second request every second, 394 times in this
  capture. The interval is a constant in the page's script.
- The page asks from the last byte it holds, not the one after, so the first byte of each answer
  is one it already has. What a range past the end answers is unknown.
- No cookie is needed. Each worker answer sets a new `session_id` of its own, which is not the
  `www.odoo.sh` session and is not to be kept.
- From a browser the requests are cross-origin: the worker allows any origin.

Log kinds are in [Enumerations](#enumerations). A build that is waiting for a worker has no
`worker_url`, so no log to read yet.

`logs/list` was seen four times and log reads on five files, on a staging and a development
build of one project, on two workers.

### Rebuild

`POST /app/branch/<id>/rebuild`, no params. Fixture: `rebuild.json`.

- It needs the session cookie and nothing else: no CSRF token, in a header or in the body.
- The answer is 200 with no `result` key at all: `{"jsonrpc": "2.0", "id": 25}`.
- It created a new build, with a higher id and the same head commit, in `progress`. The previous
  build stayed `dropped`. The branch's `push_behavior` was `new`.
- The history gained a `rebuild` tracking.
- The new build got its worker and `start_datetime` 30 seconds after the request.

Seen once, on a development branch. What it does on a branch whose `push_behavior` is `update`,
on a staging or production branch, or while a build is in progress, is unknown.

### Project settings and firewall

`POST /app/project/<technical_name>/get_settings`, no params, is what the settings page loads.
Fixture: `project_settings.json`, which keeps only a few fields of `repository`.

- `result` holds `repository`, `production_build`, `submodules`, `users`, `upsells`, `title` and
  more. `repository` is the project's settings, among them `project_name`, `odoo_branch`,
  `geolocation`, `pays_dedicated`, the subscription fields, `ci_token` and `ip_whitelist`.
  `ci_token` is a secret: the library reads `ip_whitelist` and nothing else.
- `ip_whitelist` is a string of IPv4 addresses, one per line. The page names the setting
  Firewall: it allows SSH, and PostgreSQL on a dedicated server, to the production and staging
  builds from these addresses, 10 at most, and says a change can take up to 5 minutes to apply.
  Odoo.sh also takes a range in CIDR notation, as its refusal below says.

`POST /app/project/<technical_name>/set_settings`, params `{"ip_whitelist": "<one per line>"}`.
Fixture: `set_settings.json`.

- The page sends only the settings that changed, and Odoo.sh answers them as stored:
  `{"result": {"ip_whitelist": "..."}}`. No CSRF token, as for the rebuild.
- On a subscription change the answer can hold `new_sub_values`, which the page asks to confirm.
  An `ip_whitelist` change never has been seen to.
- An entry that is not public is refused with a JSON-RPC error whose `data.name` is
  `odoo.exceptions.ValidationError` and whose message reads `IP address or CIDR range "<entry>"is
  not a public IPv4 address or range.` Nothing is stored: `get_settings` still answered the
  previous value. Seen once, for `192.0.2.1`.

Seen on 2026-10-14, once each, on one project: `set_settings` was sent with the value already
stored, so nothing changed. The page also has `POST /app/branch/<id>/allow_ip`, which its script
says authorizes the caller's address for an hour: the library does not send it.

### Enumerations

"Declared" values come from `mappings` in `get_info`, which lists values with their labels. A
declared value that was not seen has never been observed in a payload.

| Enumeration | Seen | Declared, not seen |
| --- | --- | --- |
| Branch `stage` | `production`, `staging`, `dev` | |
| Build `stage` | `production`, `staging`, `dev`, `dummy` | `duplicate` |
| Build `status` | `progress`, `done`, `dropped` | `updating`, `skipped`, `killed` |
| Build `result` | `success`, `failed`, `warning` | |
| `push_behavior` | `new`, `update` | `nothing` |
| `tracking_type` | `push`, `rebuild`, `stage` | Not declared anywhere |
| Log names | `install`, `pip`, `odoo`, `update`, `neutralize` | `upgrade`, named in the page's script |

- `warning` was only seen in the history, never in a `builds` answer.
- A development build had `install`, `pip` and `odoo`; a staging build `odoo`, `update` and
  `neutralize`. `logs/list` is the authority for a given build.
- `status_info` is free text: `done`, an empty string, `Starting build...`,
  `Installing dependencies...`, `Installing database...`, `Installing: <module>`,
  `Testing: <module>`, `Could not establish http connection to the build (...)` and
  `Platform error. Please contact the support if this persists.` were seen.
- The combinations seen: `progress` with no result; `done` with `success`; `dropped` with
  `success`, `failed` or `warning`.

### Not found

| Request | Status | Answer |
| --- | --- | --- |
| Page: `GET /project/no-such-project` | 404 | An HTML page titled `Page Not Found` |
| `POST /app/project/no-such-owner-no-such-repo-1/branches` | 200 | `error.data.name` is `odoo.exceptions.AccessError` |
| `POST /app/project/no-such-project/get_info` | 200 | `error.data.name` is `builtins.Exception` |
| `POST /app/branch/1/builds` | 200 | `error.data.name` is `builtins.Exception` |

Fixtures: `not_found.html`, cut down to its title, and `access_error.json`.

The JSON requests were sent by hand on 2026-10-04, once each: the page never asks for a project
it does not have.

- No JSON answer means "not found".
- The `AccessError` message names the signed-in user, their id and `paas.repository`. The
  `technical_name` ended in `-1`, so it was probably read as repository 1, which the user cannot
  read.
- The `builtins.Exception` message is only `If the error persists contact the support and
  communicate the following error code: SH-<hex>`. Nothing tells it from a server failure.
- The `branches` and `builds` answers had `"id": null`; the `get_info` answer had the request's id.

The unauthenticated answer is in [Without a valid session](#without-a-valid-session), and in
`unauthenticated.json`.

### Open questions

- Whether the websocket replays the notifications after `last`, what it does when its session
  expires, what a wrong `version` costs, and whether a socket that sends no idle frame is closed.
- The JSON answer for a `technical_name` whose trailing id belongs to no project, and whether
  anything before that id is read.
- Whether `builtins.Exception` on a branch or build request means it does not exist, is forbidden,
  or either.
- Whether `builds` honours a `build_limit` above 4, and `history` an `offset` above 0.
- How long the `access_token` lasts, and whether it changes when the session does.
- Which fields vary between projects: one project was captured.
- The answers to a collaborator who is not an admin.
