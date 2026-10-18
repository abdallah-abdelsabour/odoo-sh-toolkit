# odouche-mcp

An unofficial [Model Context Protocol](https://modelcontextprotocol.io) server for
[Odoo.sh](https://www.odoo.sh), so development agents can work with your projects. Built on the
[`odouche`](https://pypi.org/project/odouche/) library.

> This project is not affiliated with, endorsed by, or supported by Odoo S.A. Odoo.sh has no public
> API; this server talks to what the Odoo.sh web interface uses, which can change without notice.
> It acts with your own Odoo.sh access and nothing more, and staying within your agreement with
> Odoo is your responsibility. See [Unofficial status](https://avanserv.github.io/odouche/unofficial/).

**Status: pre-alpha.** The server reports its session, lists projects, branches and builds, and reads one
build and its logs, whose lines reach the agent as the build printed them, secrets included. It changes
nothing on Odoo.sh unless it is started with `--allow-changes`, which adds a tool that starts a rebuild.

Log in with `osh auth login`, from [`odouche-cli`](https://pypi.org/project/odouche-cli/), then give
your client the server's command:

<!-- x-release-please-start-version -->
```json
{
  "mcpServers": {
    "odouche": { "command": "uvx", "args": ["odouche-mcp==0.4.0"] }
  }
}
```
<!-- x-release-please-end -->

The version is pinned because an unpinned `uvx` can move to a newer release without you choosing
it, and this process holds your Odoo.sh session.

Documentation: the [guide](https://avanserv.github.io/odouche/mcp/), with the setup and the security
measures, and the [tool reference](https://avanserv.github.io/odouche/mcp-reference/).
