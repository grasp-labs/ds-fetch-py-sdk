import json
import sys

import httpx
import pytest
from typer.testing import CliRunner

from ds_fetch_cli import app as cli
from ds_fetch_py_sdk import BrowserLogin, Fetch
from ds_fetch_py_sdk.env import resolve

DATASET = {
    "name": "gold.d1",
    "columns": [{"name": "dept", "type": "VARCHAR"}, {"name": "n", "type": "BIGINT"}],
    "partition_columns": ["dept"],
}
RESULT = {"columns": DATASET["columns"], "rows": [["eng", None]], "row_count": 1, "truncated": True, "elapsed_ms": 5}
runner = CliRunner()


def handler(req):
    path = req.url.path
    if path.endswith("/query/"):
        sql = json.loads(req.content)["sql"]
        if sql == "bad":
            return httpx.Response(422, json={"details": [{"field": "sql", "loc": "body", "code": "syntax_error"}]})
        return httpx.Response(200, json={**RESULT, "rows": [[sql, None]]}, headers={"X-Cache": "MISS"})
    if path.endswith("/datasets/"):
        return httpx.Response(200, json={"data": [DATASET], "page": {"has_next": False}})
    if "/datasets/" in path:
        return httpx.Response(200, json=DATASET)
    if path.endswith("/invoke/"):
        return httpx.Response(200, json={"tool_use_id": "u", "output": "SELECT 1"})
    if path.endswith("/tools/"):
        return httpx.Response(200, json={"data": [{"tool_id": "ls", "effect": "read", "descriptions": {"en": "List.\nMore"}}]})
    if path.endswith("/health-check/"):
        return httpx.Response(200, json={"message": "Server is running."})
    return httpx.Response(200, json={"version": "v1.0.0"})


@pytest.fixture(autouse=True)
def fake_fetch(monkeypatch):
    monkeypatch.setattr(cli, "Fetch", lambda env: Fetch(env, token="t", transport=httpx.MockTransport(handler)))


def invoke(*args, stdin=None):
    return runner.invoke(cli.app, ["-e", "dev", *args], input=stdin, env={"COLUMNS": "120"})


def test_query_prints_table_and_summary():
    result = invoke("query", "SELECT 1")
    assert result.exit_code == 0, result.output
    assert "SELECT 1" in result.stdout and "NULL" in result.stdout
    assert "1 rows · 5 ms · cache MISS" in result.stderr and "truncated" in result.stderr


def test_query_reads_stdin_and_prints_json():
    result = invoke("--json", "query", "-", stdin="SELECT 2")
    assert json.loads(result.stdout) == [{"dept": "SELECT 2", "n": None}]


def test_ask_shows_sql_then_runs_it():
    result = invoke("ask", "how many?", "-d", "gold.d1")
    assert "SELECT 1" in result.stderr and "SELECT 1" in result.stdout
    assert invoke("ask", "how many?", "--sql-only").stdout.strip() == "SELECT 1"


def test_fresh_bypasses_the_index_on_every_read(monkeypatch):
    sent = []

    def recording(req):
        sent.append(req.headers.get("Cache-Control"))
        return handler(req)

    monkeypatch.setattr(cli, "Fetch", lambda env: Fetch(env, token="t", transport=httpx.MockTransport(recording)))
    for args in (["datasets"], ["dataset", "gold.d1"], ["query", "SELECT 1"]):
        assert invoke(*args, "--fresh").exit_code == 0
    assert runner.invoke(cli.app, ["-e", "dev", "datasets"], env={"AIC_FRESH": "1"}).exit_code == 0
    invoke("datasets")
    assert sent == ["no-cache"] * 4 + [None]


def test_datasets_dataset_tools_health():
    assert "gold.d1" in invoke("datasets").stdout
    assert json.loads(invoke("--json", "datasets").stdout)[0]["partition_columns"] == ["dept"]
    out = invoke("dataset", "gold.d1").stdout
    assert 'In SQL: gold."d1"' in out and "✓" in out
    assert json.loads(invoke("--json", "dataset", "gold.d1").stdout)["name"] == "gold.d1"
    tools = invoke("tools").stdout
    assert "List." in tools and "More" not in tools
    assert json.loads(invoke("--json", "tools").stdout)[0]["tool_id"] == "ls"
    assert "Server is running." in invoke("health").stdout
    assert json.loads(invoke("--json", "health").stdout)["version"] == "v1.0.0"


def test_login_and_logout(monkeypatch):
    calls = []

    class FakeLogin:
        def __init__(self, **kwargs):
            calls.append(kwargs.get("on_url"))

        def bind(self, env):
            return self

        def login(self):
            calls.append("login")

        def logout(self):
            calls.append("logout")

    monkeypatch.setattr(cli, "BrowserLogin", FakeLogin)
    assert "Signed in to dev" in invoke("login").stderr
    assert "Signed out of dev" in invoke("logout").stderr
    assert calls == [cli._show_sign_in, "login", None, "logout"]


def test_login_with_email_prompts_password_and_mfa(monkeypatch):
    created = []

    class FakePassword:
        def __init__(self, email, password, mfa_code):
            created.append((email, password, mfa_code(6)))

        def bind(self, env):
            return self

        def login(self):
            pass

    monkeypatch.setattr(cli, "PasswordLogin", FakePassword)
    result = invoke("login", "--email", "me@aic.no", stdin="pw\n123456\n")
    assert result.exit_code == 0, result.output
    assert created == [("me@aic.no", "pw", "123456")]
    assert "https://auth-dev.grasp-daas.com" in result.stderr
    assert "Signed in to dev" in result.stderr


def test_browser_sign_in_url_is_shown_by_the_cli(monkeypatch, capsys):
    monkeypatch.setattr(cli, "Fetch", lambda env: Fetch(env, auth=BrowserLogin(cache=False)))
    fetch = cli._fetch(cli.State(resolve("dev"), json=False))
    fetch.auth.on_url("https://auth-dev.grasp-daas.com/oauth/authorize/?x=1")
    assert "oauth/authorize" in capsys.readouterr().err


def test_no_command_shows_banner_and_help():
    result = runner.invoke(cli.app, [])
    assert result.exit_code == 0
    assert "AI Commons · Fetch" in result.stderr
    assert "aic-fetch" in result.stdout


def test_version():
    assert "ds-fetch-cli" in runner.invoke(cli.app, ["--version"]).stdout


def test_bad_environment_variable(monkeypatch):
    result = runner.invoke(cli.app, ["health"], env={"AIC_ENV": "staging"})
    assert result.exit_code == 2
    assert "unknown environment" in result.output


def test_run_turns_sdk_errors_into_exit_code_1(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["aic-fetch", "-e", "dev", "query", "bad"])
    with pytest.raises(SystemExit) as e:
        cli.run()
    assert e.value.code == 1
    assert "syntax_error" in capsys.readouterr().err
