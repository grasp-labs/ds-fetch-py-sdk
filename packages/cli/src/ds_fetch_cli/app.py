"""Commands of `aic-fetch`."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich import box
from rich.console import Console
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from ds_fetch_py_sdk import ENVIRONMENTS, AICError, BrowserLogin, Fetch, PasswordLogin
from ds_fetch_py_sdk import __version__ as sdk_version
from ds_fetch_py_sdk.env import resolve

from ._version import __version__

if TYPE_CHECKING:
    from ds_fetch_py_sdk import QueryResult
    from ds_fetch_py_sdk.env import Environment

BANNER = """[cyan]
 █████╗ ██╗ ██████╗
██╔══██╗██║██╔════╝
███████║██║██║
██╔══██║██║██║
██║  ██║██║╚██████╗
╚═╝  ╚═╝╚═╝ ╚═════╝[/cyan]
[bold]AI Commons · Fetch[/bold]
"""


def _host(url: str) -> str:
    return url.partition("://")[2]


HELP = "\n".join(
    (
        """SQL and natural-language queries over your [cyan]AI Commons[/cyan] data lake.

\b
[bold]Get started[/bold]
  aic-fetch -e dev login --email you@company.com
  aic-fetch -e dev datasets
  aic-fetch -e dev query 'SELECT * FROM gold."<dataset-id>" LIMIT 10'

\b
[bold]Environments[/bold] (-e or $AIC_ENV, default prod): API, then sign-in""",
        *(f"  {e.name:<6} {_host(e.fetch_url):<34} {_host(e.issuer)}" for e in ENVIRONMENTS.values()),
        """
\b
[bold]Credentials[/bold], first found wins
  $AIC_TOKEN                               a platform JWT
  $AIC_CLIENT_ID and $AIC_CLIENT_SECRET    client credentials
  $AIC_EMAIL and $AIC_PASSWORD             email and password
  the session from aic-fetch login         browser (SSO) or --email

Global options go before the command. Run aic-fetch COMMAND -h for its options and examples.""",
    )
)

Fresh = Annotated[
    bool,
    typer.Option(
        "--fresh",
        envvar="AIC_FRESH",
        help="Read S3 directly, bypassing the dataset index and cache. Slower but current; "
        "needed when the index is empty, e.g. locally without ds-fetch-indexer.",
    ),
]

app = typer.Typer(
    name="aic-fetch",
    rich_markup_mode="rich",
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
out = Console()
err = Console(stderr=True)


class Env(StrEnum):
    local = "local"
    dev = "dev"
    prod = "prod"


@dataclass(frozen=True)
class State:
    env: Environment
    json: bool


def _show_version(value: bool) -> None:
    if value:
        out.print(f"ds-fetch-cli {__version__} (ds-fetch-py-sdk {sdk_version})")
        raise typer.Exit


@app.callback(invoke_without_command=True, help=HELP)
def main(
    ctx: typer.Context,
    env: Annotated[
        Env | None, typer.Option("--env", "-e", help="Environment. Default: $AIC_ENV or prod.", show_default=False)
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON instead of tables, for scripts.")] = False,
    version: Annotated[bool, typer.Option("--version", callback=_show_version, is_eager=True, help="Show versions.")] = False,  # noqa: ARG001
) -> None:
    if ctx.invoked_subcommand is None:
        err.print(BANNER)
        typer.echo(ctx.get_help())
        raise typer.Exit
    try:
        ctx.obj = State(resolve(env), as_json)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="environment") from e


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _plain(value: Any) -> str:
    """Text from a server or the data lake, unable to drive the terminal: control characters become �."""
    return _CONTROL.sub("\ufffd", str(value))


def _text(value: Any, style: str = "") -> Text:
    """`_plain`, shown literally: Rich markup in it is not interpreted."""
    return Text(_plain(value), style=style)


def _link(url: str) -> Text:
    url = _plain(url)
    return Text(url, style=Style(link=url))


def _show_sign_in(url: str) -> None:
    err.print("Opening your browser to sign in. If it does not open, visit:\n", _link(url), sep="")


def _fetch(state: State) -> Fetch:
    fetch = Fetch(state.env)
    if isinstance(fetch.auth, BrowserLogin):
        fetch.auth.on_url = _show_sign_in
    return fetch


def _json(data: Any) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    typer.echo(_CONTROL.sub(lambda m: f"\\u{ord(m[0]):04x}", text))


def _table(*headers: str) -> Table:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", show_edge=False)
    for header in headers:
        table.add_column(_text(header))
    return table


def _cell(value: Any) -> Text:
    return Text("NULL", style="dim") if value is None else _text(value)


def _print_result(state: State, result: QueryResult) -> None:
    if state.json:
        _json(result.records())
        return
    table = _table(*(c.name for c in result.columns))
    for row in result.rows:
        table.add_row(*map(_cell, row))
    out.print(table)
    note = f"{result.row_count} rows · {result.elapsed_ms} ms"
    if result.cache:
        note += f" · cache {result.cache}"
    err.print(_text(note, "dim"), Text("· truncated", "yellow") if result.truncated else "")


def _prompt_mfa(digits: int) -> str:
    return str(typer.prompt(f"{digits}-digit code from your authenticator app", err=True))


@app.command()
def login(
    ctx: typer.Context,
    email: Annotated[
        str | None, typer.Option("--email", "-u", help="Sign in with email and password instead of the browser.")
    ] = None,
) -> None:
    """Sign in to the AI Commons identity server.

    Opens your browser, which supports SSO. With --email, asks for the password (or reads $AIC_PASSWORD) and an MFA code if needed.

    The session is kept per environment for a day.

    \b
    [bold]Examples[/bold]
      aic-fetch -e dev login
      aic-fetch -e dev login --email you@company.com
    """
    state: State = ctx.obj
    err.print(BANNER)
    err.print("Identity server:", _link(state.env.issuer))
    if email:
        secret = os.environ.get("AIC_PASSWORD") or str(typer.prompt("Password", hide_input=True, err=True))
        PasswordLogin(email, secret, mfa_code=_prompt_mfa).bind(state.env).login()
    else:
        auth = BrowserLogin(on_url=_show_sign_in).bind(state.env)
        with err.status("Waiting for sign-in in the browser…"):
            auth.login()
    err.print(f"[green]✓[/green] Signed in to [bold]{state.env.name}[/bold].")


@app.command()
def logout(ctx: typer.Context) -> None:
    """Forget the session for the environment, e.g. aic-fetch -e dev logout."""
    state: State = ctx.obj
    BrowserLogin().bind(state.env).logout()
    err.print(f"Signed out of [bold]{state.env.name}[/bold].")


@app.command()
def query(
    ctx: typer.Context,
    sql: Annotated[str, typer.Argument(help="One SELECT, or - to read it from stdin.")],
    fresh: Fresh = False,
) -> None:
    """Run one SQL SELECT and print the rows.

    Name datasets as aic-fetch datasets lists them, quoted after the layer: gold."<id>" or silver."<pipeline-id>/<job-id>".

    \b
    [bold]Examples[/bold]
      aic-fetch query 'SELECT * FROM gold."<dataset-id>" LIMIT 10'
      aic-fetch query - < report.sql
      aic-fetch --json query 'SELECT count(*) FROM gold."<dataset-id>"' | jq
    """
    state: State = ctx.obj
    statement = sys.stdin.read() if sql == "-" else sql
    with _fetch(state) as fetch, err.status("Running query…"):
        result = fetch.query(statement, fresh=fresh)
    _print_result(state, result)


@app.command()
def ask(
    ctx: typer.Context,
    question: Annotated[str, typer.Argument(help="Your question, in plain language.")],
    dataset: Annotated[list[str] | None, typer.Option("--dataset", "-d", help="Dataset to consider. Repeatable.")] = None,
    sql_only: Annotated[bool, typer.Option("--sql-only", help="Print the SQL without running it.")] = False,
    fresh: Fresh = False,
) -> None:
    """Ask in plain language: ds-tools writes the SQL, which is shown and then run.

    \b
    [bold]Examples[/bold]
      aic-fetch ask "Headcount per department?" -d gold.<dataset-id>
      aic-fetch ask "Headcount per department?" --sql-only
    """
    state: State = ctx.obj
    with _fetch(state) as fetch:
        with err.status("Writing SQL…"):
            sql = fetch.sql(question, datasets=dataset or ())
        if sql_only:
            typer.echo(_plain(sql))
            return
        err.print(Syntax(_plain(sql), "sql", background_color="default"))
        with err.status("Running query…"):
            result = fetch.query(sql, fresh=fresh)
    _print_result(state, result)


@app.command()
def datasets(ctx: typer.Context, fresh: Fresh = False) -> None:
    """List the datasets you can query, with their column counts.

    \b
    [bold]Examples[/bold]
      aic-fetch -e dev datasets
      aic-fetch -e local datasets --fresh
      aic-fetch --json datasets | jq -r '.[].name'
    """
    state: State = ctx.obj
    with _fetch(state) as fetch, err.status("Listing datasets…"):
        found = list(fetch.datasets(fresh=fresh))
    if state.json:
        _json([asdict(ds) for ds in found])
        return
    table = _table("name", "columns", "partitions")
    for ds in found:
        table.add_row(_text(ds.name), str(len(ds.columns)), _text(", ".join(ds.partition_columns)))
    out.print(table)


@app.command()
def dataset(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="gold.<dataset-id> or <bronze|silver>.<pipeline-id>/<job-id>.")],
    fresh: Fresh = False,
) -> None:
    """Show one dataset's columns, and how to name it in SQL.

    \b
    [bold]Example[/bold]
      aic-fetch dataset gold.5003105c-8a84-5f77-ab74-5e57003112b8
    """
    state: State = ctx.obj
    with _fetch(state) as fetch:
        ds = fetch.dataset(name, fresh=fresh)
    if state.json:
        _json(asdict(ds))
        return
    table = _table("column", "type", "partition")
    for c in ds.columns:
        table.add_row(_text(c.name), _text(c.type), "✓" if c.name in ds.partition_columns else "")
    out.print(_text(f"In SQL: {ds.ref}", "bold"))
    out.print(table)


@app.command()
def tools(ctx: typer.Context) -> None:
    """List the ds-tools you can invoke, e.g. the one ask uses to write SQL."""
    state: State = ctx.obj
    with _fetch(state) as fetch:
        found = fetch.tools.list()
    if state.json:
        _json([asdict(t) for t in found])
        return
    table = _table("tool", "effect", "description")
    for t in found:
        table.add_row(_text(t.tool_id), _text(t.effect), _text(t.description.partition("\n")[0]))
    out.print(table)


@app.command()
def health(ctx: typer.Context) -> None:
    """Check Fetch is up and show its version. Needs no sign-in."""
    state: State = ctx.obj
    with _fetch(state) as fetch:
        status = {**fetch.health(), "version": fetch.version(), "env": state.env.name}
    if state.json:
        _json(status)
        return
    out.print(Text("● ", "green") + _text(status["message"]) + _text(f"  {state.env.fetch_url} · {status['version']}", "dim"))


def run() -> None:
    """Console entry point: SDK errors become a one-line message and exit code 1."""
    try:
        app()
    except AICError as e:
        err.print(Text("error: ", "red") + _text(e))
        raise SystemExit(1) from None
