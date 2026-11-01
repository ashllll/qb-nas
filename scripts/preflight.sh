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

step "ruff check"
"$RUFF" check "${TARGETS[@]}"

step "ruff format --check"
"$RUFF" format --check "${TARGETS[@]}"

if [[ "$MODE" != full ]]; then
  printf '\n%s gate passed\n' "$MODE"
  exit 0
fi

step "pytest"
"$PYTHON" -m pytest tests -q

printf '\nfull gate passed\n'
