# odouche

Unofficial Python client for [Odoo.sh](https://www.odoo.sh): projects, branches, builds and logs,
as typed models, for use in your own tools.

> This project is not affiliated with, endorsed by, or supported by Odoo S.A. Odoo.sh has no public
> API; this library talks to what the Odoo.sh web interface uses, which can change without notice.
> It acts with your own Odoo.sh access and nothing more, and staying within your agreement with
> Odoo is your responsibility. See [Unofficial status](https://avanserv.github.io/odouche/unofficial/).

**Status: pre-alpha.** Below 1.0, a minor release can break the API.

```bash
uv add odouche
```

```python
import odouche


with odouche.Client() as client:
    for project in client.projects():
        print(project.name, project.repository, project.url)
```

It logs in through a browser, lists projects, branches and builds, watches a build, reads and
follows build logs, triggers a rebuild and gives the SSH target of a build. The session is kept in the OS keyring, or taken from
`ODOUCHE_SESSION` in headless use.

- [Guide](https://avanserv.github.io/odouche/library/)
- [API reference](https://avanserv.github.io/odouche/reference/)
- [Security](https://avanserv.github.io/odouche/security/)
