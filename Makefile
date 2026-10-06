SRC := packages

CURRENT := $(shell python3 -c "import tomllib; print(tomllib.load(open('packages/sdk/pyproject.toml', 'rb'))['project']['version'])")

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[33m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: install
install: ## Install both packages and dev tools
	uv sync --all-packages --all-extras

.PHONY: clean
clean: ## Remove build and cache artifacts
	rm -rf build/ dist/ docs/build/ .pytest_cache/ .coverage coverage.xml htmlcov/ .mypy_cache/ .ruff_cache/
	find . -type d -name __pycache__ -not -path './.venv/*' -prune -exec rm -rf {} +

.PHONY: lint
lint: ## Lint with ruff
	uv run ruff check $(SRC)
	uv run ruff format --check $(SRC)

.PHONY: format
format: ## Format with ruff
	uv run ruff check --fix $(SRC)
	uv run ruff format $(SRC)

.PHONY: type-check
type-check: ## Type-check with mypy
	uv run mypy

.PHONY: security-check
security-check: ## Security scan with bandit
	uv run bandit -q -ll -r packages/sdk/src packages/cli/src

.PHONY: test
test: ## Run tests
	uv run pytest

.PHONY: test-cov
test-cov: ## Run tests with coverage (95% minimum)
	uv run pytest --cov --cov-report=term-missing --cov-report=xml

.PHONY: docs
docs: ## Build the Sphinx site into docs/build/html
	uv run --group docs sphinx-build -W -b html docs/source docs/build/html

.PHONY: build
build: clean ## Build sdists and wheels for both packages
	uv build --all-packages
	uv run twine check dist/*

.PHONY: version
version: ## Show the shared version
	@echo $(CURRENT)

.PHONY: bump
bump: ## Set the shared version: make bump VERSION=x.y.z
	@test -n "$(VERSION)" || (echo "usage: make bump VERSION=x.y.z" && exit 1)
	sed -i.bak -E 's/^version = ".*"/version = "$(VERSION)"/' packages/sdk/pyproject.toml packages/cli/pyproject.toml
	sed -i.bak -E 's/"ds-fetch-py-sdk==[^"]*"/"ds-fetch-py-sdk==$(VERSION)"/' packages/cli/pyproject.toml
	rm packages/*/pyproject.toml.bak
	uv lock

.PHONY: tag
tag: ## Tag and push v<version> (triggers release)
	git tag -a "v$(CURRENT)" -m "Version v$(CURRENT)"
	git push origin "v$(CURRENT)"
