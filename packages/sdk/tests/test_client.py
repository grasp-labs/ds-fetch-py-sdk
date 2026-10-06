import json

import httpx
import pytest

from ds_fetch_py_sdk import (
    APIError,
    BearerToken,
    BrowserLogin,
    ClientCredentials,
    Fetch,
    NetworkError,
    PasswordLogin,
    QueryError,
)
from ds_fetch_py_sdk import client as client_module
from ds_fetch_py_sdk.auth import _Token

DEV = "https://grasp-daas.com/api/fetch-dev/v1"
TOOLS = "https://grasp-daas.com/api/tools-dev/v1"
RESULT = {
    "columns": [{"name": "department", "type": "VARCHAR"}, {"name": "n", "type": "BIGINT"}],
    "rows": [["engineering", 1520], ["finance", 980]],
    "row_count": 2,
    "truncated": False,
    "elapsed_ms": 412,
}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)


def make(handler):
    return Fetch("dev", token="t", transport=httpx.MockTransport(handler))


def test_query_sends_bearer_and_parses_result():
    def handler(req):
        assert str(req.url) == f"{DEV}/query/"
        assert req.headers["Authorization"] == "Bearer t"
        assert json.loads(req.content) == {"sql": "SELECT 1"}
        return httpx.Response(200, json=RESULT, headers={"X-Cache": "HIT"})

    result = make(handler).query("SELECT 1")
    assert result.records() == [
        {"department": "engineering", "n": 1520},
        {"department": "finance", "n": 980},
    ]
    assert result.cache == "HIT"
    assert len(result) == 2


def test_fresh_sends_no_cache():
    def handler(req):
        assert req.headers["Cache-Control"] == "no-cache"
        return httpx.Response(200, json=RESULT)

    make(handler).query("SELECT 1", fresh=True)


def test_invalid_sql_raises_query_error():
    body = {"details": [{"field": "sql", "loc": "body", "code": "unknown_column", "message": "column not found"}]}
    with pytest.raises(QueryError) as e:
        make(lambda _: httpx.Response(422, json=body)).query("SELECT x")
    assert e.value.code == "unknown_column"
    assert e.value.details[0].field == "sql"


def test_retries_429_then_succeeds():
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(
                429,
                json={"code": "too_many_requests", "message": "slow"},
                headers={"Retry-After": "1"},
            )
        return httpx.Response(200, json=RESULT)

    make(handler).query("SELECT 1")
    assert len(calls) == 2


@pytest.mark.parametrize("status", [400, 403, 504])
def test_does_not_retry_terminal_errors(status):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, json={"code": "x", "message": "no", "request_id": "r1"})

    with pytest.raises(APIError) as e:
        make(handler).query("SELECT 1")
    assert len(calls) == 1
    assert e.value.status == status and e.value.request_id == "r1"


def test_retries_bodyless_gateway_errors():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(502, text="Bad Gateway")

    with pytest.raises(APIError):
        make(handler).query("SELECT 1")
    assert len(calls) == 3


def test_network_failures_retry_then_raise_network_error():
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            raise httpx.ConnectError("refused")
        if len(calls) == 2:
            return httpx.Response(200, json={"version": "v1.0.0"})
        raise httpx.ReadTimeout("slow")

    fetch = make(handler)
    assert fetch.version() == "v1.0.0"
    with pytest.raises(NetworkError, match=r"cannot reach .*/query/"):
        fetch.query("SELECT 1")
    assert len(calls) == 5


def test_datasets_paginates():
    def handler(req):
        page = int(req.url.params["page"])
        ds = {"name": f"gold.{page}", "columns": [], "partition_columns": []}
        return httpx.Response(200, json={"data": [ds], "page": {"has_next": page < 2}})

    assert [d.name for d in make(handler).datasets()] == ["gold.1", "gold.2"]


def test_dataset_name_is_url_encoded():
    def handler(req):
        assert req.url.raw_path == b"/api/fetch-dev/v1/datasets/silver.p%2Fj/"
        return httpx.Response(200, json={"name": "silver.p/j", "columns": [], "partition_columns": []})

    assert make(handler).dataset("silver.p/j").ref == 'silver."p/j"'


def test_datasets_sends_repeated_names():
    def handler(req):
        assert req.url.params.get_list("name") == ["gold.a", "gold.b"]
        return httpx.Response(200, json={"data": [], "page": {"has_next": False}})

    assert list(make(handler).datasets(names=["gold.a", "gold.b"])) == []


def test_datasets_holds_the_page_size_to_what_the_api_serves():
    sent = []

    def handler(req):
        sent.append(req.url.params["page_size"])
        return httpx.Response(200, json={"data": [], "page": {"has_next": False}})

    for asked in (1_000_000, 0, 20):
        list(make(handler).datasets(page_size=asked))
    assert sent == ["100", "1", "20"]


def test_validate_returns_the_validation():
    def handler(req):
        assert req.url.path.endswith("/query/validate/")
        assert json.loads(req.content) == {"sql": "SELECT n FROM gold.a"}
        body = {"sql": 'SELECT n FROM gold."a"', "datasets": ["gold.a"], "columns": [{"name": "n", "type": "BIGINT"}]}
        return httpx.Response(200, json=body)

    v = make(handler).validate("SELECT n FROM gold.a")
    assert (v.sql, v.datasets, v.columns[0].name) == ('SELECT n FROM gold."a"', ["gold.a"], "n")


def test_validate_raises_on_invalid_sql():
    def handler(req):
        details = [{"field": "sql", "loc": "body", "code": "unknown_column", "message": "column not found"}]
        return httpx.Response(422, json={"details": details})

    with pytest.raises(QueryError, match="column not found"):
        make(handler).validate("SELECT nope FROM gold.a")


def test_dataset_sizes_are_read_when_present():
    def handler(req):
        ds = {"name": "gold.a", "columns": [], "partition_columns": [], "file_count": 4, "row_count": 2500, "byte_size": 81920}
        return httpx.Response(200, json=ds)

    ds = make(handler).dataset("gold.a")
    assert (ds.file_count, ds.row_count, ds.byte_size) == (4, 2500, 81920)


def test_run_has_no_sizes():
    def handler(req):
        return httpx.Response(200, json={"name": "silver.p/j", "columns": [], "partition_columns": []})

    ds = make(handler).dataset("silver.p/j")
    assert (ds.row_count, ds.byte_size) == (None, None)


def test_ask_translates_with_tools_then_queries():
    def handler(req):
        if str(req.url) == f"{TOOLS}/tools/text_to_sql/invoke/":
            body = json.loads(req.content)
            assert body["input"] == {"question": "headcount?", "datasets": ["gold.a"]}
            return httpx.Response(200, json={"tool_use_id": body["tool_use_id"], "output": "```sql\nSELECT 1\n```"})
        assert json.loads(req.content) == {"sql": "SELECT 1"}
        return httpx.Response(200, json=RESULT)

    assert make(handler).ask("headcount?", datasets=["gold.a"]).sql == "SELECT 1"


def test_health_is_unauthenticated():
    def handler(req):
        assert "Authorization" not in req.headers
        return httpx.Response(200, json={"version": "v1.0.0"})

    assert make(handler).version() == "v1.0.0"


def test_tools_catalogue_paginates_and_gets():
    def handler(req):
        if req.url.path.endswith("/tools/ls/"):
            return httpx.Response(200, json={"tool_id": "ls", "descriptions": {"en": "List"}})
        cursor = req.url.params.get("since_version")
        page = {"data": [{"tool_id": cursor or "find"}], "next_page_token": None if cursor else "ls"}
        return httpx.Response(200, json=page)

    fetch = make(handler)
    assert [t.tool_id for t in fetch.tools.list()] == ["find", "ls"]
    tool = fetch.tools.get("ls")
    assert tool.description == "List" and tool.effect == "write"


def test_to_pandas():
    df = make(lambda _: httpx.Response(200, json=RESULT)).query("SELECT 1").to_pandas()
    assert list(df.columns) == ["department", "n"]


def test_default_auth_precedence(monkeypatch):
    assert isinstance(Fetch("dev").auth, BrowserLogin)
    PasswordLogin("me@aic.no").bind(Fetch("dev", token="x").env)._save(_Token("a", "r", 0))
    cached = Fetch("dev").auth
    assert isinstance(cached, PasswordLogin) and cached.email is None
    monkeypatch.setenv("AIC_EMAIL", "me@aic.no")
    monkeypatch.setenv("AIC_PASSWORD", "pw")
    assert isinstance(Fetch("dev").auth, PasswordLogin)
    monkeypatch.setenv("AIC_CLIENT_ID", "cid")
    monkeypatch.setenv("AIC_CLIENT_SECRET", "s")
    assert isinstance(Fetch("dev").auth, ClientCredentials)
    monkeypatch.setenv("AIC_TOKEN", "t")
    assert isinstance(Fetch("dev").auth, BearerToken)


def test_local_and_dev_share_the_dev_identity_server():
    assert Fetch("local", token="t").env.issuer == Fetch("dev", token="t").env.issuer == "https://auth-dev.grasp-daas.com"
    assert Fetch("prod", token="t").env.issuer == "https://auth.grasp-daas.com"


def test_environment_override(monkeypatch):
    monkeypatch.setenv("AIC_ENV", "local")
    assert Fetch(token="t").env.fetch_url == "http://localhost:8080/api/fetch-dev/v1"
    with pytest.raises(ValueError):
        Fetch("staging", token="t")


@pytest.mark.parametrize("url", ["http://grasp-daas.com/api/fetch/v1", "http://127.0.0.1.evil.example/x", "ftp://localhost/x"])
def test_credentials_never_travel_in_clear_text(monkeypatch, url):
    monkeypatch.setenv("AIC_FETCH_URL", url)
    with pytest.raises(ValueError, match="must use https"):
        Fetch("dev", token="t")


def test_tool_ids_stay_one_path_segment():
    def handler(req):
        assert req.url.raw_path == b"/api/tools-dev/v1/tools/..%2F..%2Fadmin/invoke/"
        return httpx.Response(200, json={"tool_use_id": "u"})

    make(handler).tools.invoke("../../admin", {})
