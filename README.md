```
 █████╗ ██╗ ██████╗
██╔══██╗██║██╔════╝
███████║██║██║
██╔══██║██║██║
██║  ██║██║╚██████╗
╚═╝  ╚═╝╚═╝ ╚═════╝
AI Commons · Fetch
```

# AI Commons Fetch for Python

Read-only SQL over your [AI Commons](https://aic-project.com) tenant data lake, plus
natural-language questions through ds-tools. Docs:
[grasp-labs.github.io/ds-fetch-py-sdk](https://grasp-labs.github.io/ds-fetch-py-sdk/).

| Package | For | Install |
|---------|-----|---------|
| [ds-fetch-py-sdk](https://github.com/grasp-labs/ds-fetch-py-sdk/tree/main/packages/sdk) | Python apps, notebooks, pipelines | `uv add ds-fetch-py-sdk` |
| [ds-fetch-cli](https://github.com/grasp-labs/ds-fetch-py-sdk/tree/main/packages/cli) | The `aic-fetch` command | `uv tool install ds-fetch-cli` |

```python
from ds_fetch_py_sdk import Fetch

with Fetch("dev") as fetch:
    print(fetch.query('SELECT count(*) AS n FROM gold."5003105c-8a84-5f77-ab74-5e57003112b8"').records())
```

```bash
aic-fetch -e dev login
aic-fetch -e dev ask "Headcount per department?"
```

## Repository

```
pyproject.toml     uv workspace root: shared dev tools and ruff, mypy, pytest, coverage config
packages/sdk/      ds-fetch-py-sdk: the client; depends only on httpx
packages/cli/      ds-fetch-cli: Typer + Rich; uses only the SDK's public API
docs/              Sphinx site for both
```

Both packages share one version and release together: the CLI pins the SDK at that exact version.

## Development

```bash
uv sync --all-packages --all-extras
uv run pre-commit install
make help    # lint, format, type-check, security-check, test, test-cov, docs, build, bump, tag
```

To release, run `make bump VERSION=x.y.z`, commit, then `make tag`. The release workflow checks
that the tag matches both packages, publishes both to PyPI, creates the GitHub release, and records
release fragments in ds-coordination.

## License

Apache 2.0
