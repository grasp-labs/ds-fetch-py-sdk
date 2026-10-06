import base64
import json
import time

import httpx
import pytest

from ds_fetch_py_sdk import AuthError, BrowserLogin, Fetch, PasswordLogin, cached_session
from ds_fetch_py_sdk.env import ENVIRONMENTS

DEV = ENVIRONMENTS["dev"]
EXP = int(time.time()) + 3600


def jwt(exp=EXP):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"h.{payload}.s"


def identity(seen, *, mfa=False, login_status=200):
    def handler(req):
        body = json.loads(req.content)
        seen.append((req.url.path, body))
        if req.url.path == "/auth/login/":
            if login_status != 200:
                return httpx.Response(login_status, json={"non_field_errors": ["Unable to log in with provided credentials."]})
            if mfa:
                return httpx.Response(206, json={"authenticity_token": "at", "digits": 6})
            return httpx.Response(200, json={"access_token": jwt(), "refresh_token": "r1"})
        if req.url.path == "/mfa/login/":
            if body["code"] != "123456":
                return httpx.Response(400, json={"non_field_errors": ["Unable to log in with provided credentials."]})
            return httpx.Response(200, json={"access_token": jwt(), "refresh_token": "r2", "user": "me@aic.no"})
        if req.url.path == "/auth/token/refresh/":
            return httpx.Response(200, json={"access": jwt(EXP + 1)})
        raise AssertionError(req.url)

    return httpx.Client(transport=httpx.MockTransport(handler))


def make(seen, **kwargs):
    identity_kwargs = {k: kwargs.pop(k) for k in ("mfa", "login_status") if k in kwargs}
    login = PasswordLogin("me@aic.no", "pw", **kwargs).bind(DEV)
    login._http = identity(seen, **identity_kwargs)
    return login


def test_login_without_mfa_posts_to_the_dev_identity_server():
    seen = []
    login = make(seen)
    assert login.access_token() == jwt()
    assert seen == [("/auth/login/", {"email": "me@aic.no", "password": "pw"})]
    assert login._token.expires_at == EXP


def test_login_with_mfa_asks_for_the_code():
    seen, asked = [], []
    login = make(seen, mfa=True, mfa_code=lambda digits: asked.append(digits) or "123456")
    login.login()
    assert asked == [6]
    assert seen[1] == ("/mfa/login/", {"authenticity_token": "at", "code": "123456"})
    assert login._token.refresh == "r2"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"mfa": True}, "uses MFA"),
        ({"mfa": True, "mfa_code": "000000"}, "Unable to log in"),
        ({"login_status": 400}, "Unable to log in"),
    ],
)
def test_login_failures(kwargs, match):
    with pytest.raises(AuthError, match=match):
        make([], **kwargs).login()


def test_cached_session_refreshes_without_the_password():
    make([]).login()
    session = cached_session(DEV)
    assert isinstance(session, PasswordLogin) and session.email is None
    seen = []
    session._http = identity(seen)
    session._token = session._load()
    session._token.expires_at = 0
    assert session.access_token() == jwt(EXP + 1)
    assert session.email == "me@aic.no"
    assert seen == [("/auth/token/refresh/", {"refresh": "r1"})]


def test_expired_session_without_password_asks_to_sign_in_again():
    session = PasswordLogin("me@aic.no").bind(DEV)
    with pytest.raises(AuthError, match="sign in again"):
        session.access_token()


def test_sessions_are_keyed_by_kind_and_environment():
    make([]).login()
    assert cached_session(ENVIRONMENTS["prod"]) is None
    assert BrowserLogin().bind(DEV)._load() is None
    BrowserLogin().bind(DEV).logout()
    assert cached_session(DEV) is None


def test_requests_go_through_fetch_with_the_password_token():
    login = make([])
    seen = []

    def api(req):
        seen.append(req.headers["Authorization"])
        return httpx.Response(200, json={"data": [], "page": {"has_next": False}})

    list(Fetch("dev", auth=login, transport=httpx.MockTransport(api)).datasets())
    assert seen == [f"Bearer {jwt()}"]


def test_unparseable_token_expires_soon():
    seen = []
    login = PasswordLogin("me@aic.no", "pw").bind(DEV)

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"access_token": "opaque"})

    login._http = httpx.Client(transport=httpx.MockTransport(handler))
    login.login()
    assert time.time() < login._token.expires_at <= time.time() + 300
