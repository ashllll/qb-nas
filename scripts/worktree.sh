#!/usr/bin/env bash
# 新建隔离 worktree：从 origin/main 起分支，复用主仓 .venv / node_modules 与 husky shim。
# 用法：bash scripts/worktree.sh <name> [branch]
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 <name> [branch]" >&2
  echo "  name: worktree 目录名，例如 64-api-timeout" >&2
  echo "  branch: 默认 feat/<name>" >&2
  exit 2
fi

case "${1:-}" in
  -h | --help)
    echo "usage: $0 <name> [branch]"
    echo "  name: worktree 目录名，例如 64-api-timeout"
    echo "  branch: 默认 feat/<name>"
    exit 0
    ;;
esac

NAME="$1"
BRANCH="${2:-feat/$NAME}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
COMMON_DIR="$(git rev-parse --path-format=absolute --git-common-dir)"
MAIN_ROOT="$(dirname "$COMMON_DIR")"
WORKTREE_ROOT="${QB_NAS_WORKTREE_ROOT:-$(dirname "$MAIN_ROOT")/qb-nas-factory-worktrees}"
WORKTREE="$WORKTREE_ROOT/$NAME"
REMOTE_URL="${QB_NAS_REMOTE_URL:-https://github.com/ashllll/qb-nas.git}"

if [[ -e "$WORKTREE" ]]; then
  echo "worktree 已存在: $WORKTREE" >&2
  exit 1
fi

git fetch -q "$REMOTE_URL" main:refs/remotes/origin/main
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git worktree add "$WORKTREE" "$BRANCH"
else
  git worktree add -b "$BRANCH" "$WORKTREE" origin/main
fi

link_if_present() {
  local src="$1" dst="$2"
  if [[ ! -e "$src" ]]; then
    echo "跳过（主仓不存在）: $src" >&2
    return 0
  fi
  if [[ -e "$dst" || -L "$dst" ]]; then
    return 0
  fi
  ln -s "$src" "$dst"
}
link_if_present "$MAIN_ROOT/.venv" "$WORKTREE/.venv"
link_if_present "$MAIN_ROOT/node_modules" "$WORKTREE/node_modules"

if [[ -d "$MAIN_ROOT/.husky/_" ]]; then
  mkdir -p "$WORKTREE/.husky"
  cp -R "$MAIN_ROOT/.husky/_" "$WORKTREE/.husky/_"
else
  echo "警告：主仓没有 .husky/_，pre-commit 不会运行；先在主仓执行 npm run prepare。" >&2
fi

PY="$MAIN_ROOT/.venv/bin/python"
if [[ -x "$PY" ]]; then
  VERSION="$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  if [[ "$VERSION" != "3.11" ]]; then
    echo "警告：主仓 .venv 是 Python ${VERSION}，项目按 3.11 验证。" >&2
    echo "在主仓执行 uv sync --extra dev --locked，然后重建这个 worktree（或手动建 .venv）。" >&2
  fi
fi

printf 'worktree: %s\nbranch: %s\n' "$WORKTREE" "$BRANCH"
