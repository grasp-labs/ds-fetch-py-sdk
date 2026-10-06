"""Authentication against the AI Commons identity server.

- `BearerToken`: a platform JWT you already have.
- `BrowserLogin`: OAuth authorization code + PKCE in the browser, including enterprise SSO.
- `PasswordLogin`: email and password at `/auth/login/`, with TOTP MFA when enabled.
- `ClientCredentials`: OAuth client credentials, for machines.

Every flow signs in at the environment's issuer, never at one a server names. OAuth flows read
its endpoints from its authorization-server metadata (RFC 8414), which must name that issuer,
and bind tokens to the API with `resource` (RFC 8707).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Self
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .env import require_secure
from .errors import AuthError, _json

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from .env import Environment

_LEEWAY = 30


class BearerToken(httpx.Auth):
    """A platform JWT you already have."""

    def __init__(self, token: str) -> None:
        self._header = f"Bearer {token}"

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = self._header
        yield request


@dataclass
class _Token:
    access: str
    refresh: str | None
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at - _LEEWAY


def default_cache() -> Path:
    """Where sessions are cached: `$XDG_CONFIG_HOME/aic/credentials.json`, default `~/.config`."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "aic" / "credentials.json"


class TokenAuth(httpx.Auth):
    """A session with the identity server: acquires, caches and renews tokens, and retries once on 401.

    Sessions are cached per environment in `default_cache()`, or at `cache`; `cache=False` keeps them
    in memory.
    """

    kind: ClassVar[str] = ""

    def __init__(self, *, issuer: str | None = None, cache: Path | bool = False) -> None:
        self.issuer = require_secure(issuer, "issuer") if issuer else None
        self.cache = default_cache() if cache is True else cache or None
        self._env: Environment | None = None
        self._token: _Token | None = None
        self._lock = threading.RLock()
        self._http = httpx.Client(timeout=30)

    def bind(self, env: Environment) -> Self:
        self._env = env
        return self

    @property
    def env(self) -> Environment:
        if self._env is None:
            raise AuthError("auth is not bound to an environment; pass it to Fetch(auth=...)")
        return self._env

    @property
    def _issuer(self) -> str:
        return (self.issuer or self.env.issuer).rstrip("/")

    def access_token(self, *, force: bool = False) -> str:
        with self._lock:
            if self._token is None:
                self._token = self._load()
            if self._token is None or self._token.expired or force:
                self._token = self._renew()
                self._save(self._token)
            return self._token.access

    def login(self) -> None:
        """Sign in now, replacing any cached session."""
        with self._lock:
            self._load()
            self._token = self._acquire()
            self._save(self._token)

    def logout(self) -> None:
        """Forget the session, in memory and in the cache."""
        with self._lock:
            self._token = None
            entries = _read(self.cache)
            if entries.pop(self.env.fetch_url, None) is not None:
                _write(self.cache, entries)

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self.access_token()}"
        response = yield request
        if response.status_code == 401:
            request.headers["Authorization"] = f"Bearer {self.access_token(force=True)}"
            yield request

    def _renew(self) -> _Token:
        if self._token and self._token.refresh:
            try:
                return self._refresh(self._token.refresh)
            except AuthError:
                pass
        return self._acquire()

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._http.request(method, url, **kwargs)
        except httpx.HTTPError as e:
            raise AuthError(f"cannot reach {url}: {e}") from e

    def _acquire(self) -> _Token:
        raise NotImplementedError

    def _refresh(self, refresh: str) -> _Token:
        raise NotImplementedError

    def _state(self) -> dict[str, Any]:
        """Extra fields to cache with the session."""
        return {}

    def _restore(self, entry: dict[str, Any]) -> None:
        """Read back what `_state` cached."""

    def _load(self) -> _Token | None:
        entry = _read(self.cache).get(self.env.fetch_url) or {}
        if entry.get("kind") != self.kind:
            return None
        self._restore(entry)
        if not entry.get("access_token"):
            return None
        return _Token(entry["access_token"], entry.get("refresh_token"), entry.get("expires_at", 0))

    def _save(self, token: _Token) -> None:
        if self.cache is None:
            return
        entries = _read(self.cache)
        entries[self.env.fetch_url] = {
            "kind": self.kind,
            **self._state(),
            "access_token": token.access,
            "refresh_token": token.refresh,
            "expires_at": token.expires_at,
        }
        _write(self.cache, entries)


class OAuth(TokenAuth):
    """OAuth 2.1 against the identity server, with endpoints from RFC 8414 metadata."""

    def __init__(self, *, scope: str = "openid read", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.scope = scope
        self._meta: dict[str, Any] | None = None

    @property
    def metadata(self) -> dict[str, Any]:
        """The identity server's RFC 8414 metadata, checked to be its own and to use HTTPS."""
        if self._meta is None:
            issuer = self._issuer
            r = self._send("GET", f"{issuer}/.well-known/oauth-authorization-server")
            if not r.is_success:
                raise AuthError(f"no authorization server metadata at {issuer} ({r.status_code})")
            meta = _json(r)
            if str(meta.get("issuer", "")).rstrip("/") != issuer:
                raise AuthError(f"metadata at {issuer} names issuer {meta.get('issuer')!r}; refusing it")
            for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
                if key in meta:
                    try:
                        require_secure(str(meta[key]), key)
                    except ValueError as e:
                        raise AuthError(str(e)) from None
            self._meta = meta
        return self._meta

    def _refresh(self, refresh: str) -> _Token:
        return self._grant("refresh_token", refresh_token=refresh)

    def _grant(self, grant_type: str, **params: str) -> _Token:
        data = {"grant_type": grant_type, "resource": self.env.resource, **self._client(), **params}
        r = self._send("POST", self.metadata["token_endpoint"], data=data)
        body = _json(r)
        if not r.is_success or "access_token" not in body:
            error = body.get("error_description") or body.get("error") or f"HTTP {r.status_code}"
            raise AuthError(f"token request failed: {error}")
        return _Token(
            body["access_token"],
            body.get("refresh_token") or params.get("refresh_token"),
            time.time() + int(body.get("expires_in") or 300),
        )

    def _client(self) -> dict[str, str]:
        raise NotImplementedError


class ClientCredentials(OAuth):
    """Machine-to-machine sign-in with a registered client id and secret."""

    kind = "client_credentials"

    def __init__(self, client_id: str, client_secret: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.client_id = client_id
        self._secret = client_secret

    def _client(self) -> dict[str, str]:
        return {"client_id": self.client_id, "client_secret": self._secret}

    def _acquire(self) -> _Token:
        return self._grant("client_credentials", scope=self.scope)


def _write_url(url: str) -> None:
    sys.stderr.write(f"Sign in to AI Commons:\n  {url}\n")


class BrowserLogin(OAuth):
    """Interactive sign-in in the browser, including enterprise SSO.

    Uses authorization code with PKCE on a loopback redirect. Without a `client_id` the
    SDK registers a public client (RFC 7591) and caches its id with the session.
    `on_url` receives the sign-in URL, for when the browser cannot be opened; it defaults
    to writing it to stderr.
    """

    kind = "browser"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        port: int = 8976,
        cache: Path | bool = True,
        open_browser: bool = True,
        on_url: Callable[[str], None] | None = None,
        timeout: float = 300,
        **kwargs: Any,
    ) -> None:
        super().__init__(cache=cache, **kwargs)
        self.client_id = client_id
        self.open_browser = open_browser
        self.on_url = on_url or _write_url
        self.timeout = timeout
        self.redirect_uri = f"http://127.0.0.1:{port}/callback"
        self._port = port

    def _state(self) -> dict[str, Any]:
        return {"client_id": self.client_id}

    def _restore(self, entry: dict[str, Any]) -> None:
        self.client_id = self.client_id or entry.get("client_id")

    def _client(self) -> dict[str, str]:
        if self.client_id is None:
            self.client_id = self._register()
        return {"client_id": self.client_id}

    def _register(self) -> str:
        endpoint = self.metadata.get("registration_endpoint")
        if not endpoint:
            raise AuthError("the identity server does not support client registration; pass client_id")
        metadata = {
            "client_name": "AIC Fetch Python SDK",
            "redirect_uris": [self.redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        r = self._send("POST", endpoint, json=metadata)
        body = _json(r)
        if not r.is_success or "client_id" not in body:
            raise AuthError(f"client registration failed: {body.get('error_description') or r.status_code}")
        return str(body["client_id"])

    def _acquire(self) -> _Token:
        client = self._client()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        state = secrets.token_urlsafe(16)
        query = urlencode(
            {
                **client,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "scope": self.scope,
                "state": state,
                "code_challenge": challenge.rstrip(b"=").decode(),
                "code_challenge_method": "S256",
                "resource": self.env.resource,
            }
        )
        code = self._await_code(f"{self.metadata['authorization_endpoint']}?{query}", state)
        return self._grant("authorization_code", code=code, redirect_uri=self.redirect_uri, code_verifier=verifier)

    def _await_code(self, url: str, state: str) -> str:
        params: dict[str, str] = {}

        class Callback(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                split = urlsplit(self.path)
                if split.path != "/callback":
                    self.send_error(404)
                    return
                params.update({k: v[0] for k, v in parse_qs(split.query).items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<p>Signed in to AI Commons. You can close this tab.</p>")

            def log_message(self, *_: Any) -> None:
                pass

        self.on_url(url)
        deadline = time.monotonic() + self.timeout
        with HTTPServer(("127.0.0.1", self._port), Callback) as server:
            server.timeout = 1
            if self.open_browser:
                webbrowser.open(url)
            while not params and time.monotonic() < deadline:
                server.handle_request()
        if not params:
            raise AuthError("sign-in timed out")
        if params.get("state") != state:
            raise AuthError("sign-in failed: state mismatch")
        if "code" not in params:
            raise AuthError(f"sign-in failed: {params.get('error_description') or params.get('error')}")
        return params["code"]


class PasswordLogin(TokenAuth):
    """Sign in with email and password at the identity server's `/auth/login/`.

    When the account has MFA, `mfa_code` supplies the TOTP code: a string, or a callable that
    receives the number of digits and returns the code. The session is renewed with its refresh
    token; once that expires, signing in again needs the password.
    """

    kind = "password"

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        *,
        mfa_code: str | Callable[[int], str] | None = None,
        cache: Path | bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(cache=cache, **kwargs)
        self.email = email
        self._password = password
        self.mfa_code = mfa_code

    def _state(self) -> dict[str, Any]:
        return {"email": self.email}

    def _restore(self, entry: dict[str, Any]) -> None:
        self.email = self.email or entry.get("email")

    def _acquire(self) -> _Token:
        if not (self.email and self._password):
            raise AuthError("session expired; sign in again with your email and password")
        r = self._send("POST", f"{self._issuer}/auth/login/", json={"email": self.email, "password": self._password})
        body = _json(r)
        if r.status_code == httpx.codes.PARTIAL_CONTENT:
            digits = int(body.get("digits") or 6)
            code = self.mfa_code(digits) if callable(self.mfa_code) else self.mfa_code
            if not code:
                raise AuthError("this account uses MFA; provide the code from your authenticator app")
            mfa = {"authenticity_token": body["authenticity_token"], "code": code}
            r = self._send("POST", f"{self._issuer}/mfa/login/", json=mfa)
            body = _json(r)
        if not r.is_success or "access_token" not in body:
            raise AuthError(f"sign-in failed: {_describe(body, r)}")
        return _Token(body["access_token"], body.get("refresh_token"), _expiry(body["access_token"]))

    def _refresh(self, refresh: str) -> _Token:
        r = self._send("POST", f"{self._issuer}/auth/token/refresh/", json={"refresh": refresh})
        body = _json(r)
        access = body.get("access") or body.get("access_token")
        if not r.is_success or not access:
            raise AuthError(f"session refresh failed: {_describe(body, r)}")
        return _Token(access, body.get("refresh") or body.get("refresh_token") or refresh, _expiry(access))


def cached_session(env: Environment, cache: Path | None = None) -> TokenAuth | None:
    """The cached sign-in for `env`, as the auth that created it, or None."""
    entry = _read(cache or default_cache()).get(env.fetch_url) or {}
    session: TokenAuth
    if entry.get("kind") == BrowserLogin.kind:
        session = BrowserLogin(cache=cache or True)
    elif entry.get("kind") == PasswordLogin.kind:
        session = PasswordLogin(cache=cache or True)
    else:
        return None
    return session.bind(env)


def _expiry(jwt: str) -> float:
    """The token's `exp`, read without verification (the API verifies it)."""
    try:
        payload = jwt.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return time.time() + 300


def _describe(body: dict[str, Any], r: httpx.Response) -> str:
    """The first message in a DRF error body."""
    for value in body.values():
        if isinstance(value, str):
            return value
        if isinstance(value, list) and value:
            return str(value[0])
    return f"HTTP {r.status_code}"


def _read(cache: Path | None) -> dict[str, Any]:
    if cache is None or not cache.exists():
        return {}
    try:
        data = json.loads(cache.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(cache: Path | None, entries: dict[str, Any]) -> None:
    """Replace the cache atomically with a new 0600 file, so a symlink or wider mode on the old one never matters."""
    if cache is None:
        return
    cache.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=cache.parent, prefix=f".{cache.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(entries, f, indent=2)
        Path(tmp).replace(cache)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
