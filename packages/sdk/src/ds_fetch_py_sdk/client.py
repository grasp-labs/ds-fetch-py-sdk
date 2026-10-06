"""The Fetch client."""

from __future__ import annotations

import os
import random
import re
import time
import uuid
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import quote

import httpx

from ._version import PACKAGE_NAME, __version__
from .auth import BearerToken, BrowserLogin, ClientCredentials, PasswordLogin, TokenAuth, cached_session
from .env import Environment, resolve
from .errors import APIError, NetworkError
from .models import Dataset, QueryResult, Tool, ToolResult, Validation, _columns

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

_FENCE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE)
MAX_PAGE_SIZE = 100
"""The largest dataset page the API serves; it lowers anything above this."""


def _default_auth(env: Environment) -> httpx.Auth:
    if token := os.environ.get("AIC_TOKEN"):
        return BearerToken(token)
    if (cid := os.environ.get("AIC_CLIENT_ID")) and (secret := os.environ.get("AIC_CLIENT_SECRET")):
        return ClientCredentials(cid, secret)
    if (email := os.environ.get("AIC_EMAIL")) and (password := os.environ.get("AIC_PASSWORD")):
        return PasswordLogin(email, password, mfa_code=os.environ.get("AIC_MFA_CODE"))
    return cached_session(env) or BrowserLogin()


class Fetch:
    """Client for AI Commons Fetch: read-only SQL over your tenant data lake.

    Without `token` or `auth`, credentials come from `$AIC_TOKEN`; then `$AIC_CLIENT_ID` and
    `$AIC_CLIENT_SECRET`; then `$AIC_EMAIL` and `$AIC_PASSWORD`; then the cached session;
    then browser sign-in.

        with Fetch("dev") as fetch:
            result = fetch.query('SELECT count(*) FROM gold."<dataset-id>"')
    """

    def __init__(
        self,
        env: str | Environment | None = None,
        *,
        token: str | None = None,
        auth: httpx.Auth | None = None,
        language: str = "en",
        timeout: float = 30.0,
        retries: int = 3,
        sql_tool: str = "text_to_sql",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.env = resolve(env)
        auth = auth or (BearerToken(token) if token else _default_auth(self.env))
        if isinstance(auth, TokenAuth):
            auth.bind(self.env)
        self.auth = auth
        self.retries = max(1, retries)
        self.sql_tool = sql_tool
        self.tools = Tools(self)
        self._http = httpx.Client(
            auth=auth,
            timeout=timeout,
            transport=transport,
            headers={
                "Accept-Language": language,
                "User-Agent": f"{PACKAGE_NAME}/{__version__}",
            },
        )

    def query(self, sql: str, *, fresh: bool = False) -> QueryResult:
        """Run one SELECT. `fresh` reads S3 as it is now, bypassing the index and cache."""
        r = self._request("POST", self._url("/query/"), json={"sql": sql}, fresh=fresh)
        body = r.json()
        return QueryResult(
            sql=sql,
            columns=_columns(body["columns"]),
            rows=body["rows"],
            row_count=body["row_count"],
            truncated=body["truncated"],
            elapsed_ms=body["elapsed_ms"],
            cache=r.headers.get("X-Cache"),
            request_id=r.headers.get("X-Request-ID"),
        )

    def validate(self, sql: str, *, fresh: bool = False) -> Validation:
        """Check one SELECT as `query` would, without reading data. Invalid SQL raises `QueryError`."""
        return Validation.parse(self._request("POST", self._url("/query/validate/"), json={"sql": sql}, fresh=fresh).json())

    def sql(self, question: str, *, datasets: Sequence[str] = ()) -> str:
        """Translate a natural-language question to SQL with ds-tools."""
        payload: dict[str, Any] = {"question": question}
        if datasets:
            payload["datasets"] = list(datasets)
        return _FENCE.sub("", self.tools.invoke(self.sql_tool, payload).output.strip())

    def ask(self, question: str, *, datasets: Sequence[str] = (), fresh: bool = False) -> QueryResult:
        """Answer a natural-language question: translate it to SQL, then run it."""
        return self.query(self.sql(question, datasets=datasets), fresh=fresh)

    def datasets(self, *, names: Sequence[str] = (), page_size: int = MAX_PAGE_SIZE, fresh: bool = False) -> Iterator[Dataset]:
        """Every dataset and pipeline run you are granted, across pages; only `names` if given.

        Names that do not exist or are not granted are left out. `page_size` is held to 1…`MAX_PAGE_SIZE`,
        the range the API serves.
        """
        page, size = 1, max(1, min(page_size, MAX_PAGE_SIZE))
        while True:
            params: dict[str, Any] = {"page": page, "page_size": size, "name": list(names)}
            body = self._request("GET", self._url("/datasets/"), params=params, fresh=fresh).json()
            yield from map(Dataset.parse, body["data"])
            if not body["page"]["has_next"]:
                return
            page += 1

    def dataset(self, name: str, *, fresh: bool = False) -> Dataset:
        """One dataset by name, e.g. `gold.<dataset-id>` or `silver.<pipeline-id>/<job-id>`."""
        url = self._url(f"/datasets/{quote(name, safe='')}/")
        return Dataset.parse(self._request("GET", url, fresh=fresh).json())

    def health(self) -> dict[str, Any]:
        return self._public("/health-check/")

    def version(self) -> str:
        return str(self._public("/version/")["version"])

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _url(self, path: str) -> str:
        return f"{self.env.fetch_url}{path}"

    def _public(self, path: str) -> dict[str, Any]:
        return dict(self._request("GET", self._url(path), auth=None).json())

    def _request(self, method: str, url: str, *, fresh: bool = False, **kwargs: Any) -> httpx.Response:
        headers = {"Cache-Control": "no-cache"} if fresh else None
        for attempt in range(self.retries):
            last = attempt == self.retries - 1
            try:
                r = self._http.request(method, url, headers=headers, **kwargs)
            except httpx.TransportError as e:
                if last:
                    raise NetworkError(f"cannot reach {url}: {e}") from e
                time.sleep(_backoff(None, attempt))
                continue
            if r.is_success:
                return r
            if last or not _retryable(r):
                raise APIError.from_response(r)
            time.sleep(_backoff(r, attempt))
        raise AssertionError("unreachable")


class Tools:
    """ds-tools catalogue and invocation, under the same sign-in as Fetch."""

    def __init__(self, client: Fetch) -> None:
        self._c = client

    def list(self) -> list[Tool]:
        tools: list[Tool] = []
        params: dict[str, str] = {}
        while True:
            body = self._c._request("GET", self._url("/tools/"), params=params).json()
            tools += map(Tool.parse, body["data"])
            if not body.get("next_page_token"):
                return tools
            params = {"since_version": body["next_page_token"]}

    def get(self, tool_id: str) -> Tool:
        return Tool.parse(self._c._request("GET", self._url(f"/tools/{quote(tool_id, safe='')}/")).json())

    def invoke(self, tool_id: str, input: dict[str, Any], *, timeout_ms: int = 0) -> ToolResult:
        """Run a tool. Retries reuse the same `tool_use_id`, so they are idempotent."""
        payload = {
            "tool_use_id": f"sdk_{uuid.uuid4().hex}",
            "input": input,
            "timeout_ms": timeout_ms,
        }
        url = self._url(f"/tools/{quote(tool_id, safe='')}/invoke/")
        body = self._c._request("POST", url, json=payload).json()
        return ToolResult(
            body["tool_use_id"],
            body.get("output", ""),
            bool(body.get("truncated")),
            body.get("artifact_file_id") or None,
        )

    def _url(self, path: str) -> str:
        return f"{self._c.env.tools_url}{path}"


def _retryable(r: httpx.Response) -> bool:
    if r.status_code in (429, 500, 503):
        return True
    return r.status_code >= 500 and "json" not in r.headers.get("Content-Type", "")


def _backoff(r: httpx.Response | None, attempt: int) -> float:
    retry_after = r.headers.get("Retry-After", "") if r is not None else ""
    base = int(retry_after) if retry_after.isdigit() else 0.5 * 2**attempt
    return base + random.uniform(0, 0.5)
