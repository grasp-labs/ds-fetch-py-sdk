import httpx
import pytest

from ds_fetch_py_sdk import AuthError, BrowserLogin, ClientCredentials, Fetch
from ds_fetch_py_sdk.auth import _Token

ISSUER = "https://auth-dev.grasp-daas.com"
META = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/oauth/authorize/",
    "token_endpoint": f"{ISSUER}/oauth/token/",
    "registration_endpoint": f"{ISSUER}/oauth/register",
}


def identity(tokens, seen):
    def handler(req):
        if req.url.path == "/api/fetch-dev/v1/.well-known/oauth-protected-resource":
            return httpx.Response(404)
        if req.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=META)
        if req.url.path == "/oauth/token/":
            seen.append(dict(httpx.QueryParams(req.content.decode())))
            return httpx.Response(200, json=tokens.pop(0)) if tokens else httpx.Response(400, json={"error": "invalid_grant"})
        raise AssertionError(req.url)

    return httpx.Client(transport=httpx.MockTransport(handler))


def api(seen):
    def handler(req):
        seen.append(req.headers["Authorization"])
        return httpx.Response(
            401 if req.headers["Authorization"] == "Bearer a1" else 200,
            json={"data": [], "page": {"has_next": False}},
        )

    return httpx.MockTransport(handler)


def test_client_credentials_discovers_and_renews_on_401():
    grants, auth_headers = [], []
    auth = ClientCredentials("cid", "secret")
    auth._http = identity(
        [{"access_token": "a1", "expires_in": 600}, {"access_token": "a2", "expires_in": 600}],
        grants,
    )

    list(Fetch("dev", auth=auth, transport=api(auth_headers)).datasets())

    assert auth_headers == ["Bearer a1", "Bearer a2"]
    assert grants[0] == {
        "grant_type": "client_credentials",
        "resource": "https://grasp-daas.com/api/fetch-dev/v1",
        "client_id": "cid",
        "client_secret": "secret",
        "scope": "openid read",
    }


def test_browser_login_refreshes_from_cache(tmp_path):
    grants = []
    cache = tmp_path / "credentials.json"
    first = BrowserLogin(client_id="pub", cache=cache).bind(Fetch("dev", token="x").env)
    first._save(_Token("old", "r1", 0))

    login = BrowserLogin(cache=cache)
    login._http = identity([{"access_token": "new", "refresh_token": "r2", "expires_in": 600}], grants)
    Fetch("dev", auth=login)

    assert login.access_token() == "new"
    assert grants[0]["grant_type"] == "refresh_token" and grants[0]["client_id"] == "pub"
    assert cache.stat().st_mode & 0o777 == 0o600
    assert '"r2"' in cache.read_text()


def failing(meta_status=200, token=None):
    def handler(req):
        if req.url.path.endswith("oauth-protected-resource"):
            raise httpx.ConnectError("down")
        if req.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(meta_status, json=META)
        if token is None:
            raise httpx.ConnectError("down")
        return httpx.Response(400, json=token)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("http", "match"),
    [
        (failing(meta_status=404), "no authorization server metadata"),
        (failing(token={"error": "invalid_client"}), "invalid_client"),
        (failing(), "cannot reach .*: down"),
    ],
)
def test_token_failures_raise_auth_error(http, match):
    auth = ClientCredentials("cid", "secret").bind(Fetch("dev", token="x").env)
    auth._http = http
    with pytest.raises(AuthError, match=match):
        auth.access_token()


def test_failed_refresh_falls_back_to_new_grant():
    grants = []
    tokens = [{"error": "invalid_grant"}, {"access_token": "a3", "expires_in": 600}]

    def handler(req):
        if req.url.path == "/oauth/token/":
            grants.append(dict(httpx.QueryParams(req.content.decode()))["grant_type"])
            body = tokens.pop(0)
            return httpx.Response(400 if "error" in body else 200, json=body)
        return httpx.Response(200 if "authorization-server" in req.url.path else 404, json=META)

    auth = ClientCredentials("cid", "secret").bind(Fetch("dev", token="x").env)
    auth._http = httpx.Client(transport=httpx.MockTransport(handler))
    auth._token = _Token("expired", "stale", 0)
    assert auth.access_token() == "a3"
    assert grants == ["refresh_token", "client_credentials"]


def test_unbound_oauth_fails_clearly():
    with pytest.raises(AuthError, match="not bound"):
        ClientCredentials("cid", "secret").access_token()
