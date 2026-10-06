"""Response models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    """DuckDB type name, e.g. `VARCHAR` or `DECIMAL(18,2)`."""


def _columns(raw: list[dict[str, Any]]) -> list[Column]:
    return [Column(c["name"], c["type"]) for c in raw]


@dataclass(frozen=True)
class QueryResult:
    """Result of one SELECT. Values are JSON-encoded per DuckDB type (see the API docs)."""

    sql: str
    columns: list[Column]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    """The 100,000-row or 8 MiB inline limit cut the result."""
    elapsed_ms: int
    cache: str | None = None
    """`HIT`, `MISS` or `SHARED`."""
    request_id: str | None = None

    def records(self) -> list[dict[str, Any]]:
        names = [c.name for c in self.columns]
        return [dict(zip(names, row, strict=True)) for row in self.rows]

    def to_pandas(self) -> pd.DataFrame:
        """Requires the `pandas` extra."""
        import pandas as pd  # noqa: PLC0415

        return pd.DataFrame(self.rows, columns=[c.name for c in self.columns])

    def __len__(self) -> int:
        return self.row_count


@dataclass(frozen=True)
class Validation:
    """SQL that would run, checked through binding without reading data."""

    sql: str
    """The canonical SQL."""
    datasets: list[str]
    """The datasets the SQL reads."""
    columns: list[Column]
    """The result columns a query would return."""

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Validation:
        return cls(raw["sql"], raw["datasets"], _columns(raw["columns"]))


@dataclass(frozen=True)
class Dataset:
    name: str
    """`gold.<dataset-id>` or `<bronze|silver>.<pipeline-id>/<job-id>`."""
    columns: list[Column]
    partition_columns: list[str] = field(default_factory=list)
    file_count: int | None = None
    """Data files in the partitions you may read."""
    row_count: int | None = None
    """Rows in the partitions you may read. Gold datasets only."""
    byte_size: int | None = None
    """Parquet bytes in the partitions you may read. Gold datasets only."""

    @property
    def ref(self) -> str:
        """The name quoted for SQL, e.g. `gold."5003105c-…"`."""
        schema, _, rest = self.name.partition(".")
        return f'{schema}."{rest}"'

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Dataset:
        return cls(
            raw["name"],
            _columns(raw["columns"]),
            raw.get("partition_columns") or [],
            raw.get("file_count"),
            raw.get("row_count"),
            raw.get("byte_size"),
        )


@dataclass(frozen=True)
class Tool:
    tool_id: str
    name: str
    description: str
    input_schema: dict[str, Any]
    effect: str
    tags: list[str]

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Tool:
        return cls(
            raw["tool_id"],
            raw.get("name") or raw["tool_id"],
            (raw.get("descriptions") or {}).get("en", ""),
            raw.get("input_schema") or {},
            raw.get("effect") or "write",
            raw.get("tags") or [],
        )


@dataclass(frozen=True)
class ToolResult:
    tool_use_id: str
    output: str
    truncated: bool = False
    artifact_file_id: str | None = None


@dataclass(frozen=True)
class FieldError:
    field: str
    loc: str
    code: str
    message: str
