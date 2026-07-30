.PHONY: help sync lint format typecheck test check build lock clean

help:
	@echo "Available targets:"
	@echo "  sync       Install/sync dependencies with uv (--all-extras)"
	@echo "  lint       Run ruff check"
	@echo "  format     Run ruff format"
	@echo "  typecheck  Run mypy on src"
	@echo "  test       Run pytest with coverage"
	@echo "  check      Run lint + format --check + typecheck + test (mirrors CI)"
	@echo "  build      Build sdist and wheel with uv"
	@echo "  lock       Regenerate uv.lock"
	@echo "  clean      Remove build artifacts and caches"

sync:
	uv sync --all-extras

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy src

test:
	uv run pytest --cov=argilla_cli --cov-report=term-missing

check: lint
	uv run ruff format --check .
	$(MAKE) typecheck
	$(MAKE) test

build:
	uv build

lock:
	uv lock

clean:
	rm -rf dist build *.egg-info src/*.egg-info .coverage .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} +
