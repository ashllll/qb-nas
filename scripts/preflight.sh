#!/usr/bin/env bash
# Local mirror of .github/workflows/gates.yml; run before commit or push.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE=full
case "${1:-}" in
  "") ;;
  --fast) MODE=fast ;;
  --staged) MODE=staged ;;
  -h | --help)
    echo "usage: $0 [--fast|--staged]"
    exit 0
    ;;
  *)
    echo "unknown argument: $1" >&2
    echo "usage: $0 [--fast|--staged]" >&2
    exit 2
    ;;
esac

RUFF=.venv/bin/ruff
[[ -x "$RUFF" ]] || RUFF=ruff
PYTHON=.venv/bin/python
[[ -x "$PYTHON" ]] || PYTHON=python

step() { printf '\n==> %s\n' "$1"; }

TARGETS=(magnet_harvester tests)

if [[ "$MODE" == staged ]]; then
  STAGED="$(git diff --cached --name-only --diff-filter=ACM -- magnet_harvester tests)"
  TARGETS=()
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    case "$f" in
      *.py) TARGETS+=("$f") ;;
    esac
  done <<< "$STAGED"
  if [[ ${#TARGETS[@]} -eq 0 ]]; then
    echo "no staged Python files under magnet_harvester/ or tests/"
    exit 0
  fi
fi

PY_VERSION="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
if [[ "$PY_VERSION" != "3.11" ]]; then
  printf '检测到 Python %s，项目按 3.11 验证（.python-version）。\n' "${PY_VERSION:-unknown}" >&2
  printf '重建：uv sync --extra dev --locked；或 python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install ruff pytest pytest-asyncio pytest-cov\n' >&2
  exit 1
fi

step "ruff check"
"$RUFF" check "${TARGETS[@]}"

step "ruff format --check"
"$RUFF" format --check "${TARGETS[@]}"

if [[ "$MODE" != full ]]; then
  printf '\n%s gate passed\n' "$MODE"
  exit 0
fi

step "uv lock --check"
if command -v uv >/dev/null 2>&1; then
  uv lock --check
else
  echo "uv 未安装，跳过 lock 检查（CI 的 gates 会跑）" >&2
fi

step "pytest (coverage)"
"$PYTHON" -m pytest tests -q --cov --cov-report=term-missing

printf '\nfull gate passed\n'
