"""Deployment environments."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

_API = "https://grasp-daas.com/api"
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def require_secure(url: str, what: str) -> str:
    """`url` if it is HTTPS, or HTTP on this machine; tokens and passwords never travel in clear text."""
    split = urlsplit(url)
    if split.scheme == "https" or (split.scheme == "http" and split.hostname in _LOOPBACK):
        return url
    raise ValueError(f"{what} must use https, or http on localhost: {url}")


@dataclass(frozen=True)
class Environment:
    """Where the APIs and the identity server live.

    `resource` is the RFC 8707 resource id tokens are minted for. Local uses dev's identity
    server and resource, so a dev sign-in works against a locally running service.
    """

    name: str
    fetch_url: str
    tools_url: str
    issuer: str
    resource: str

    def __post_init__(self) -> None:
        require_secure(self.fetch_url, "fetch_url")
        require_secure(self.tools_url, "tools_url")
        require_secure(self.issuer, "issuer")


ENVIRONMENTS = {
    "local": Environment(
        "local",
        "http://localhost:8080/api/fetch-dev/v1",
        "http://localhost:8081/api/tools-dev/v1",
        "https://auth-dev.grasp-daas.com",
        f"{_API}/fetch-dev/v1",
    ),
    "dev": Environment(
        "dev",
        f"{_API}/fetch-dev/v1",
        f"{_API}/tools-dev/v1",
        "https://auth-dev.grasp-daas.com",
        f"{_API}/fetch-dev/v1",
    ),
    "prod": Environment(
        "prod",
        f"{_API}/fetch/v1",
        f"{_API}/tools/v1",
        "https://auth.grasp-daas.com",
        f"{_API}/fetch/v1",
    ),
}

_OVERRIDES = {"fetch_url": "AIC_FETCH_URL", "tools_url": "AIC_TOOLS_URL", "issuer": "AIC_ISSUER"}


def resolve(env: str | Environment | None = None) -> Environment:
    """Resolve an environment by name, defaulting to `$AIC_ENV` or `prod`.

    `$AIC_FETCH_URL`, `$AIC_TOOLS_URL` and `$AIC_ISSUER` override the named environment's URLs.
    """
    if isinstance(env, Environment):
        return env
    name = env or os.environ.get("AIC_ENV") or "prod"
    try:
        base = ENVIRONMENTS[name]
    except KeyError:
        raise ValueError(f"unknown environment {name!r}; expected one of: {', '.join(ENVIRONMENTS)}") from None
    return replace(base, **{f: os.environ[v] for f, v in _OVERRIDES.items() if os.environ.get(v)})
