# Contributing

The full guide is
[CONTRIBUTING.md](https://github.com/avanserv/odouche/blob/main/CONTRIBUTING.md) in the
repository. In short:

```bash
git clone https://github.com/avanserv/odouche
cd odouche
make install   # the workspace and the git hooks
make check     # every gate CI runs
```

- The repository is a [uv](https://docs.astral.sh/uv/) workspace with three packages under
  `packages/`.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org); versions and the
  changelog are written by Release Please from them.
- Tests never contact the real Odoo.sh, and fixtures are scrubbed of session values and real
  project data before they are committed.
