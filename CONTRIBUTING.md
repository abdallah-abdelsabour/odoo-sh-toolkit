# Contributing

## Setup

You need [uv](https://docs.astral.sh/uv/) and `make`. uv installs the right Python for you.

```bash
make install
```

That installs the three packages in editable mode with the development tools, and the git hooks.
To use the commit message template as well:

```bash
git config commit.template .gitmessage
```

## The gates

```bash
make check
```

runs everything CI runs: format and lint (ruff), type check (basedpyright), dependency
declarations (deptry), dead code (vulture), the tests with coverage (pytest), and the documentation
build (zensical). `make help` lists the individual targets, and `make format` and `make lint` apply
fixes where `make check` only reports.

The git hooks run the same checks on the files you commit, plus a few that only make sense there:
secrets (gitleaks), spelling (codespell), Markdown (markdownlint) and the workflow audits
(actionlint, zizmor). `make hooks` runs all of them over the whole tree.

## How the repository is laid out

A uv workspace with three packages under `packages/`: `odouche` (the library), `odouche-cli` (the
`osh` command) and `odouche-mcp` (the MCP server). Two rules hold between them:

- The two frontends depend on the library, and only on its public interface. They never talk to
  Odoo.sh themselves and never import each other.
- A capability is added to the library first, then surfaced in a frontend.

`make deps` is what enforces the first: each package is checked against its own manifest, so a
frontend importing something it does not declare fails. For the same reason a dependency is
declared in the same change as the code that imports it, never ahead of it.

## Tests

Tests never contact the real Odoo.sh. Upstream responses are mocked or replayed from fixtures, and
a fixture is scrubbed of session values and real project data before it is committed.

Fixtures live in `packages/<package>/tests/fixtures/` and are written by hand from a browser
capture, which is never committed.

- Session values: write `session_id=REDACTED`. gitleaks fails on a real one, in any file.
- E-mail addresses: use `example.com`. The test suite fails on any other domain in a fixture.
- Nothing checks the rest, so scrub by hand: project names, repository names, commit authors and
  messages, build hostnames.

## Checking against the real Odoo.sh

Since no test contacts Odoo.sh, nothing in CI notices that it changed. One command does, by hand:

```bash
make live-check PROJECT=<name>
```

It asks Odoo.sh, with your stored session, for who you are, your projects, that project's
branches, the builds of its production branch (the first branch when it has none), one build, its
logs and the end of one log. Each step reports whether the answer still has the shape the library
reads, and names the field when it does not. A step with nothing to read, such as the logs of a
build still waiting for a worker, is skipped. It changes nothing on Odoo.sh, and it refuses to run
when `CI` is set.

The report holds counts and field names only, no project, branch, commit or log text, so it can be
pasted into an issue. Run it:

- before merging a release pull request;
- when someone reports an "Unexpected answer from Odoo.sh" error.

The other thing to run before a release is the [manual tests](docs/manual-tests.md): what the
live check does not reach, from the install to the MCP server.

## Commits and pull requests

Commits follow [Conventional Commits](https://www.conventionalcommits.org):
`<type>(<scope>): Summary`, with the body saying why the change exists. `.gitmessage` has the
details.

Pull requests are squash-merged, so **the pull request title is the commit**. CI checks that it is
a conventional one.

- `feat` bumps the minor version and `fix` the patch.
- `feat!`, or a `BREAKING CHANGE:` footer, is a breaking change: a minor bump while the version is
  below 1.0, a major one after.
- `docs`, `perf` and `revert` bump the patch too: they appear in the changelog, and whatever appears
  there is released.
- Everything else (`chore`, `refactor`, `test`, `ci`, `build`, `style`) bumps nothing.

## Releases

Nobody edits a version. [Release Please](https://github.com/googleapis/release-please) reads the
commits on `main` and keeps a release pull request open with the next version and the changelog.
The three packages are versioned together and always released together.

Merging the release pull request tags the release and publishes the three packages to PyPI, using
[trusted publishing](https://docs.pypi.org/trusted-publishers/): no PyPI token is stored anywhere.

One thing to know before merging one: wait for the
`chore: sync uv.lock with the release version` commit to land on the release branch, and for CI
to pass on it. Release Please does not know about `uv.lock`; the Release workflow adds that
commit. Run `make live-check` and the [manual tests](docs/manual-tests.md) as well, since CI
cannot.

After the merge, the three publish jobs wait for a maintainer to approve their deployment in the
Release workflow run.

## Security

See [SECURITY.md](SECURITY.md) for how to report a vulnerability, and
[docs/security.md](docs/security.md) for the constraints every change is held to.
