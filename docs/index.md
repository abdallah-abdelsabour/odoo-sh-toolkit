# odouche

An unofficial toolkit for working with [Odoo.sh](https://www.odoo.sh) from your development
environment.

!!! warning "Unofficial, and pre-alpha"

    This project is not affiliated with, endorsed by, or supported by Odoo S.A. Odoo.sh has no
    public API: odouche talks to what the Odoo.sh web interface uses, which can change without
    notice. It acts with your own Odoo.sh access and nothing more, and staying within your
    agreement with Odoo is your responsibility. See [Unofficial status](unofficial.md).

    The library and the `osh` command log in, list projects, branches and builds, watch a build,
    read and follow its logs and start a rebuild, and `osh ssh` opens a shell on a build with
    your own `ssh`: see the [library](library.md) and the [CLI](cli.md). The [MCP server](mcp.md) reports its session, lists projects, branches and
    builds, reads one build, its logs and a project's firewall and, when you allow it, starts a
    rebuild or lets an address through the firewall.

## Three packages, one library

| Package | What it is |
| --- | --- |
| [`odouche`](library.md) | A Python library: the client for Odoo.sh, usable in your own tools. |
| [`odouche-cli`](cli.md) | The `osh` command, in the spirit of `gcloud`, `aws` and `scw`. |
| [`odouche-mcp`](mcp.md) | A Model Context Protocol server, for development agents. |

The command-line tool and the MCP server are both built on the library and only on its public
interface, so anything they can do, your own code can do too.

## Credentials

odouche never sees, stores or logs your password or your GitHub token. The
[security model](security.md) says what it does keep, where, and for how long.
