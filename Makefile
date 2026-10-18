.DEFAULT_GOAL := help

PACKAGES := odouche odouche-cli odouche-mcp

.PHONY: help install format lint type-check deps deadcode test coverage live-check docs docs-cli docs-mcp docs-serve build hooks check clean

help: ## List available targets with their descriptions
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*## "} {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install the workspace and the git hooks
	uv sync
	uv run prek install

format: ## Format the code (ruff)
	uv run ruff format

lint: ## Lint the code, applying safe fixes (ruff), and audit the workflows (zizmor)
	uv run ruff check
	uv run zizmor --no-progress --config .github/zizmor.yml .github/workflows .github/dependabot.yml

type-check: ## Type-check the three packages (basedpyright)
	uv run basedpyright

# One run per package, each against that package's own manifest: this is the gate that holds the
# layering, because a frontend importing something it does not declare fails here.
deps: ## Check each package's declared dependencies against its imports (deptry)
	@for package in $(PACKAGES); do \
		echo "deptry: $$package"; \
		uv run deptry --config packages/$$package/pyproject.toml packages/$$package/src || exit 1; \
	done

deadcode: ## Find dead code (vulture)
	uv run vulture

test: ## Run the test suite
	uv run pytest

coverage: ## Run the test suite with coverage (terminal, xml and html reports)
	uv run pytest --cov --cov-report=term-missing --cov-report=xml --cov-report=html

# Never part of `check`: it asks the real Odoo.sh, with the session of whoever runs it.
live-check: ## Check the read path against the real Odoo.sh: make live-check PROJECT=<name>
	uv run python scripts/live_check.py $(PROJECT)

# The checks first, so that a site is never built from a reference the commands or the tools no longer give.
docs: ## Build the documentation site into site/, failing on warnings and on a stale reference
	uv run python scripts/cli_reference.py --check
	uv run python scripts/mcp_reference.py --check
	uv run zensical build --clean --strict

docs-cli: ## Write docs/cli-reference.md from the commands of osh
	uv run python scripts/cli_reference.py

docs-mcp: ## Write docs/mcp-reference.md from the tools of odouche-mcp
	uv run python scripts/mcp_reference.py

docs-serve: ## Serve the documentation locally with live reload
	uv run zensical serve

build: ## Build the wheel and sdist of each package into dist/
	rm -rf dist
	for package in $(PACKAGES); do uv build --package $$package || exit 1; done

hooks: ## Run every git hook over every tracked file (prek)
	uv run prek run --all-files

# The non-mutating forms, so a red gate means the tree is wrong rather than that it was just
# rewritten: `make format` and `make lint` are the ones that fix.
check: ## Full gate: format, lint, types, dependencies, dead code, tests with coverage, docs
	uv run ruff format --check
	uv run ruff check --no-fix
	uv run zizmor --no-progress --config .github/zizmor.yml .github/workflows .github/dependabot.yml
	$(MAKE) --no-print-directory type-check deps deadcode coverage docs

clean: ## Remove build, coverage and documentation output
	rm -rf build dist site .cache htmlcov coverage.xml .coverage .coverage.*
