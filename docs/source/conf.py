import tomllib
from pathlib import Path

_root = Path(__file__).parents[2]
_sdk = tomllib.loads((_root / "packages/sdk/pyproject.toml").read_text())["project"]

project = "AI Commons Fetch"
author = "AI Commons"
copyright = "2026, Aider AS"
release = version = _sdk["version"]

extensions = [
    "autoapi.extension",
    "myst_parser",
    "sphinx.ext.githubpages",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]
autoapi_dirs = ["../../packages/sdk/src", "../../packages/cli/src"]
autoapi_options = ["members", "show-inheritance", "imported-members"]
myst_heading_anchors = 3
exclude_patterns = ["_build", ".DS_Store"]
suppress_warnings = ["config.cache"]

html_theme = "sphinx_material"
html_title = "AI Commons · Fetch"
html_static_path = ["_static"]
html_theme_options = {
    "nav_title": "AI Commons · Fetch",
    "repo_url": "https://github.com/grasp-labs/ds-fetch-py-sdk/",
    "repo_name": "ds-fetch-py-sdk",
    "color_primary": "cyan",
    "globaltoc_depth": 2,
}
