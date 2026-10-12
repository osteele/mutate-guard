sources := "src tests"

default:
    @just --list

format:
    uv run ruff format {{sources}}

lint:
    uv run ruff format --check {{sources}}
    uv run ruff check {{sources}}

typecheck:
    uv run ty check --python-version 3.10 {{sources}}

test:
    uv run python -m unittest discover -s tests -v

check: lint typecheck test

# Install the console script as a uv tool (~/.local/bin/mutate-guard).
install:
    uv tool install --force --reinstall .
