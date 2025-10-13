#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$ROOT_DIR"

echo "[linter] Running ruff format"
uv run ruff format ibkr_trader tests

echo "[linter] Running ruff check with fixes"
uv run ruff check --fix ibkr_trader tests

echo "[linter] Verifying ruff check"
uv run ruff check ibkr_trader tests

echo "[linter] Running syntax check"
uv run python -m compileall ibkr_trader tests >/dev/null

echo "[linter] All checks passed"
