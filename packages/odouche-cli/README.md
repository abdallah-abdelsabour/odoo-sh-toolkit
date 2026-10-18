# odouche-cli

`osh`, an unofficial command-line interface for [Odoo.sh](https://www.odoo.sh), in the spirit of
`gcloud`, `aws` and `scw`. Built on the [`odouche`](https://pypi.org/project/odouche/) library.

> This project is not affiliated with, endorsed by, or supported by Odoo S.A. Odoo.sh has no public
> API; `osh` talks to what the Odoo.sh web interface uses, which can change without notice. It acts
> with your own Odoo.sh access and nothing more, and staying within your agreement with Odoo is
> your responsibility. See [Unofficial status](https://avanserv.github.io/odouche/unofficial/).

**Status: pre-alpha.** `osh` logs in, lists projects, branches and builds, watches a build, prints
and follows its logs, starts a rebuild and opens a shell on a build with your own `ssh`.

```bash
uv tool install odouche-cli
```

From a checkout of the repository an Odoo.sh project builds:

```bash
osh auth login        # sign in with GitHub in a browser; the session goes to the keyring
osh branches list     # the project's branches, with their stage
git push
osh builds watch      # follow the build of the pushed commit, and exit with its result
osh logs              # the end of that build's log
osh ssh               # a shell on that build, with your own ssh and keys
```

Anywhere else, name the project and the branch:
`osh builds watch --project acme --branch feature-x`.

The [guide](https://avanserv.github.io/odouche/cli/) has the exit codes, the JSON output, the
environment variables and shell completion, and the
[reference](https://avanserv.github.io/odouche/cli-reference/) every command and option.
