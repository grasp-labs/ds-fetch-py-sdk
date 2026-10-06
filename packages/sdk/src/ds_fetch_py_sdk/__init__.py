"""AI Commons Fetch: read-only SQL over your tenant data lake."""

from ._version import PACKAGE_NAME, __version__
from .auth import BearerToken, BrowserLogin, ClientCredentials, OAuth, PasswordLogin, TokenAuth, cached_session
from .client import Fetch, Tools
from .env import ENVIRONMENTS, Environment
from .errors import AICError, APIError, AuthError, NetworkError, QueryError
from .models import Column, Dataset, FieldError, QueryResult, Tool, ToolResult

__all__ = [
    "ENVIRONMENTS",
    "PACKAGE_NAME",
    "AICError",
    "APIError",
    "AuthError",
    "BearerToken",
    "BrowserLogin",
    "ClientCredentials",
    "Column",
    "Dataset",
    "Environment",
    "Fetch",
    "FieldError",
    "NetworkError",
    "OAuth",
    "PasswordLogin",
    "QueryError",
    "QueryResult",
    "TokenAuth",
    "Tool",
    "ToolResult",
    "Tools",
    "__version__",
    "cached_session",
]
