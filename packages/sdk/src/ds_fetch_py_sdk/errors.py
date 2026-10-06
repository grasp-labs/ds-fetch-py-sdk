"""Exceptions raised by the SDK."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import FieldError

if TYPE_CHECKING:
    import httpx


class AICError(Exception):
    """Base for every error raised by this SDK."""


class AuthError(AICError):
    """Sign-in or token acquisition failed."""


class NetworkError(AICError):
    """The API could not be reached, or did not answer in time, after retries."""


class APIError(AICError):
    """The API answered with an error."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        request_id: str | None = None,
        retry_after: int = 0,
        recoverable: bool = False,
    ) -> None:
        suffix = f" (request {request_id})" if request_id else ""
        super().__init__(f"{status} {code}: {message}{suffix}")
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.retry_after = retry_after
        self.recoverable = recoverable

    @classmethod
    def from_response(cls, r: httpx.Response) -> APIError:
        body = _json(r)
        request_id = body.get("request_id") or r.headers.get("X-Request-ID")
        if r.status_code == 422 and isinstance(body.get("details"), list):
            details = [FieldError(d.get("field", ""), d.get("loc", ""), d["code"], d.get("message", "")) for d in body["details"]]
            first = details[0] if details else FieldError("sql", "body", "invalid_query", "invalid")
            return QueryError(first.code, first.message, details, request_id=request_id)
        return cls(
            r.status_code,
            body.get("code") or body.get("class") or "http_error",
            body.get("message") or r.reason_phrase,
            request_id=request_id,
            retry_after=int(body.get("retry_after") or r.headers.get("Retry-After") or 0),
            recoverable=bool(body.get("recoverable")),
        )


class QueryError(APIError):
    """The SQL was rejected (HTTP 422). Fix the query; retrying will not help."""

    def __init__(self, code: str, message: str, details: list[FieldError], *, request_id: str | None) -> None:
        super().__init__(422, code, message, request_id=request_id)
        self.details = details


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        body = r.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
