import base64
import hashlib
import json
import socket
import threading
import urllib.request
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from ds_fetch_py_sdk import AuthError, BrowserLogin
from ds_fetch_py_sdk import auth as auth_module
from ds_fetch_py_sdk.env import ENVIRONMENTS

ISSUER = "https://auth-dev.grasp-daas.com"
META = {
    "authorization_endpoint": f"{ISSUER}/oauth/authorize/",
    "token_endpoint": f"{ISSUER}/oauth/token/",
    "registration_endpoint": f"{ISSUER}/oauth/register",
}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def identity(seen, meta=META):
    def handler(req):
        if req.url.path.endswith("oauth-protected-resource"):
            return httpx.Response(200, json={"authorization_servers": [ISSUER]})
        if req.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=meta)
        if req.url.path == "/oauth/register":
            seen["register"] = json.loads(req.content)
            return httpx.Response(201, json={"client_id": "dcr-client"})
        seen["token"] = dict(httpx.QueryParams(req.content.decode()))
        return httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "expires_in": 600})

    return httpx.Client(transport=httpx.MockTransport(handler))


def browser(monkeypatch, query):
    """Stand in for the user's browser: follow the authorize URL back to the loopback redirect."""
    opened = {}

    def open_url(url):
        opened.update({k: v[0] for k, v in parse_qs(urlsplit(url).query).items()})
        params = query(opened)
        target = f"{opened['redirect_uri']}?{params}"
        threading.Thread(target=urllib.request.urlopen, args=(target,), daemon=True).start()
        return True

    monkeypatch.setattr(auth_module.webbrowser, "open", open_url)
    return opened


def make(tmp_path, seen, **kwargs):
    login = BrowserLogin(port=free_port(), cache=tmp_path / "c.json", **kwargs).bind(ENVIRONMENTS["dev"])
    login._http = identity(seen)
    return login


def test_login_registers_client_and_exchanges_code_with_pkce(tmp_path, monkeypatch):
    seen = {}
    opened = browser(monkeypatch, lambda o: f"code=xyz&state={o['state']}")
    login = make(tmp_path, seen)

    login.login()

    assert seen["register"]["redirect_uris"] == [login.redirect_uri]
    assert seen["register"]["token_endpoint_auth_method"] == "none"
    assert opened["client_id"] == "dcr-client"
    assert opened["resource"] == "https://grasp-daas.com/api/fetch-dev/v1"
    verifier = seen["token"]["code_verifier"]
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert opened["code_challenge"] == digest
    assert seen["token"]["code"] == "xyz"
    assert login.access_token() == "a"
    assert json.loads((tmp_path / "c.json").read_text())[ENVIRONMENTS["dev"].fetch_url]["client_id"] == "dcr-client"

    login.logout()
    assert json.loads((tmp_path / "c.json").read_text()) == {}


@pytest.mark.parametrize(
    ("query", "match"),
    [
        (lambda o: "code=xyz&state=forged", "state mismatch"),
        (lambda o: f"error=access_denied&state={o['state']}", "access_denied"),
    ],
)
def test_login_rejects_bad_callbacks(tmp_path, monkeypatch, query, match):
    browser(monkeypatch, query)
    with pytest.raises(AuthError, match=match):
        make(tmp_path, {}, client_id="known").login()


def test_login_times_out(tmp_path):
    with pytest.raises(AuthError, match="timed out"):
        make(tmp_path, {}, client_id="known", open_browser=False, timeout=0).login()


def test_registration_unsupported(tmp_path):
    login = make(tmp_path, {})
    login._http = identity({}, meta={k: v for k, v in META.items() if k != "registration_endpoint"})
    with pytest.raises(AuthError, match="does not support client registration"):
        login.login()


def test_memory_only_cache_and_corrupt_file(tmp_path):
    assert BrowserLogin(cache=False).cache is None
    corrupt = tmp_path / "c.json"
    corrupt.write_text("not json")
    login = BrowserLogin(cache=corrupt).bind(ENVIRONMENTS["dev"])
    assert login._load() is None
