from datetime import UTC, datetime

import pytest

import live_check
from odouche import (
    Branch,
    Build,
    BuildStatus,
    Commit,
    Identity,
    Log,
    LogKind,
    LogLine,
    NoSessionError,
    NotFoundError,
    Project,
    SessionInfo,
    SessionSource,
    Stage,
    UpstreamChangedError,
)


NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)
PROJECT = "acme-shop"
IDENTIFYING = [
    PROJECT,
    "acme-corp",
    "feature-invoicing",
    "3f2a9c1e",
    "round the tax base",
    "Jane Doe",
    "jane",
    "odoo.modules.loading",
    "88212",
    "51044",
]

COMMIT = Commit(
    hash="3f2a9c1e5b7d4f608192a3b4c5d6e7f801234567",
    message="[FIX] invoicing: round the tax base per line",
    author="Jane Doe",
    timestamp=NOW,
    url="https://github.com/acme-corp/odoo-addons/commit/3f2a9c1e",
)
BUILD = Build(
    id=88212,
    name="acme-shop-main-88212",
    branch_id=51001,
    branch_name="main",
    commit=COMMIT,
    status=BuildStatus.DONE,
    status_name="done",
    result=None,
    result_name=None,
    status_info=None,
    started_at=NOW,
    url="https://acme-shop.odoo.com",
)


class StandIn:
    """Answers as `Client` does, and raises the error set for a method in its place."""

    def __init__(self):
        self.failures = {}
        self.calls = []
        self.options = None

    def __call__(self, **options):
        self.options = options
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.calls.append("close")

    def _answer(self, name, value):
        self.calls.append(name)
        if name in self.failures:
            raise self.failures[name]
        return value

    def identity(self):
        session = SessionInfo(SessionSource.KEYRING, NOW, NOW)
        return self._answer("identity", Identity(7, "Jane Doe", "jane", "jane@example.com", session))

    def projects(self):
        project = Project(4217, PROJECT, "acme-corp/odoo-addons", "https://www.odoo.sh/project/acme-shop")
        return self._answer("projects", [project])

    def branches(self, project):
        branches = [
            Branch(51044, "feature-invoicing", Stage.DEVELOPMENT, "dev"),
            Branch(51001, "main", Stage.PRODUCTION, "production"),
            Branch(51050, "feature-invoicing-2", Stage.UNKNOWN, "archived"),
        ]
        return self._answer(("branches", project), branches)

    def builds(self, branch):
        return self._answer(("builds", branch.id), [BUILD])

    def build(self, branch, build_id):
        return self._answer(("build", branch, build_id), BUILD)

    def logs(self, project, build):
        logs = [Log(LogKind.INSTALL, "install", NOW, "156 KB"), Log(LogKind.ODOO, "odoo", NOW, "2 MB")]
        return self._answer(("logs", project, build.id), logs)

    def read_log(self, project, build, kind, *, tail):
        lines = [LogLine("INFO acme-shop odoo.modules.loading: 51044 modules loaded", 62, truncated=False)] * tail
        return iter(self._answer(("read_log", project, build.id, kind, tail), lines))


@pytest.fixture
def client(monkeypatch):
    client = StandIn()
    monkeypatch.setattr(live_check, "Client", client)
    monkeypatch.delenv("CI", raising=False)
    return client


@pytest.mark.parametrize("value", ["true", "false", ""])
def test_refuses_in_ci_before_anything_is_contacted(client, monkeypatch, capsys, value):
    monkeypatch.setenv("CI", value)

    assert live_check.main([PROJECT]) == 2

    assert client.options is None
    assert client.calls == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "never runs in CI" in captured.err


def test_the_project_has_no_default(client):
    with pytest.raises(SystemExit) as raised:
        live_check.main([])

    assert raised.value.code == 2
    assert client.options is None


def test_walks_the_read_path_of_the_production_branch_on_a_read_only_client(client, capsys):
    assert live_check.main([PROJECT]) == 0

    assert client.options == {"read_only": True}
    assert client.calls == [
        "identity",
        "projects",
        ("branches", PROJECT),
        ("builds", 51001),
        ("build", 51001, 88212),
        ("logs", PROJECT, 88212),
        ("read_log", PROJECT, 88212, "install", 20),
        "close",
    ]
    assert capsys.readouterr().out.splitlines() == [
        "identity  pass",
        "projects  pass     1 project",
        "branches  pass     3 branches, 1 of an unknown stage",
        "builds    pass     1 build",
        "build     pass",
        "logs      pass     2 logs",
        "log tail  pass     20 lines",
        "7 passed, 0 failed, 0 skipped",
    ]


def test_names_the_step_and_the_field_that_changed_and_runs_the_rest(client, capsys):
    client.failures["projects"] = UpstreamChangedError("projects", "result[0].repos[0].id")
    client.failures["builds", 51001] = UpstreamChangedError("builds", "result[0].builds[0].commit")

    assert live_check.main([PROJECT]) == 1

    assert capsys.readouterr().out.splitlines() == [
        "identity  pass",
        "projects  changed  projects at result[0].repos[0].id",
        "branches  pass     3 branches, 1 of an unknown stage",
        "builds    changed  builds at result[0].builds[0].commit",
        "build     skipped  needs a build",
        "logs      skipped  needs a build",
        "log tail  skipped  needs a log",
        "2 passed, 2 failed, 3 skipped",
    ]


def test_another_error_is_reported_by_its_class_and_status_without_its_message(client, capsys):
    client.failures["branches", PROJECT] = NotFoundError(
        f"The session's user can reach no project named {PROJECT!r}.", operation="branches", status=404
    )

    assert live_check.main([PROJECT]) == 1

    output = capsys.readouterr().out
    assert "branches  error    NotFoundError 404" in output
    assert "builds    skipped  needs a branch" in output
    assert PROJECT not in output


def test_a_build_without_logs_skips_the_tail(client, capsys):
    client.failures["logs", PROJECT, 88212] = NotFoundError("Build 88212 has no log yet.", operation="logs")

    assert live_check.main([PROJECT]) == 1

    output = capsys.readouterr().out
    assert "logs      error    NotFoundError" in output
    assert "log tail  skipped  needs a log" in output


def test_a_step_with_nothing_to_read_is_skipped_and_is_not_a_failure(client, monkeypatch, capsys):
    monkeypatch.setattr(client, "logs", lambda project, build: [])

    assert live_check.main([PROJECT]) == 0

    assert capsys.readouterr().out.splitlines()[-3:] == [
        "logs      pass     0 logs",
        "log tail  skipped  needs a log",
        "6 passed, 0 failed, 1 skipped",
    ]


def test_the_report_holds_nothing_that_identifies_the_project(client, capsys):
    assert live_check.main([PROJECT]) == 0

    captured = capsys.readouterr()
    for value in IDENTIFYING:
        assert value not in captured.out + captured.err


def test_no_session_is_reported_by_its_class_alone(monkeypatch, capsys):
    def refuse(**_):
        raise NoSessionError("No session: log in as jane.")

    monkeypatch.setattr(live_check, "Client", refuse)
    monkeypatch.delenv("CI", raising=False)

    assert live_check.main([PROJECT]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "No session to check with: NoSessionError.\n"
