#!/usr/bin/env bash
# ============================================================================
# qb-nas 中心服务 · 容器化卸载
#
#   默认：停容器、删 compose 项目与镜像、删程序；保留 数据/日志/API Key。
#   --purge --yes：连 /vol3/1000/qb-nas 整个目录一起删。
#
#   NAS： bash /vol3/1000/qb-nas/uninstall.sh [--purge --yes]
#   Mac： 双击 deploy/卸载.command / 卸载并删除数据.command
# ============================================================================
set -Eeuo pipefail
PREFIX=/vol3/1000/qb-nas
PROJECT=qb-nas
CONTAINER=qb-nas-center
IMAGE=qb-nas-center:local
PURGE=0
YES=0
c_ok=$'\033[32m'; c_warn=$'\033[33m'; c_err=$'\033[31m'; c_0=$'\033[0m'
[ -t 1 ] || { c_ok=""; c_warn=""; c_err=""; c_0=""; }
step() { printf '\n%s▶ %s%s\n' "$c_ok" "$*" "$c_0"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '  %s! %s%s\n' "$c_warn" "$*" "$c_0" >&2; }
die()  { printf '\n%s✗ %s%s\n' "$c_err" "$*" "$c_0" >&2; exit 1; }
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX=${2:?}; shift 2 ;;
    --project) PROJECT=${2:?}; shift 2 ;;
    --container) CONTAINER=${2:?}; shift 2 ;;
    --image) IMAGE=${2:?}; shift 2 ;;
    --purge) PURGE=1; shift ;;
    --yes) YES=1; shift ;;
    --test) PREFIX=/vol3/1000/qb-nas-test; PROJECT=qb-nas-test; CONTAINER=qb-nas-center-test; IMAGE=qb-nas-center:test; shift ;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "不认识的参数：$1" ;;
  esac
done
COMPOSE="$PREFIX/compose.yaml"
printf 'qb-nas 中心服务 · 卸载\n  根目录 %s\n  容器   %s\n' "$PREFIX" "$CONTAINER"
if [ "$PURGE" = "1" ]; then
  printf '  范围   连数据一起删（%s/data 的资源库会没）\n' "$PREFIX"
  if [ "$YES" != "1" ]; then
    [ -t 0 ] || die "非交互环境请加 --yes"
    printf '\n  确认删除数据？输入 删除 再回车：'
    read -r ans
    [ "$ans" = "删除" ] || { printf '  已取消。\n'; exit 1; }
  fi
else
  printf '  范围   只删容器/镜像/程序，保留数据、日志、API Key\n'
fi
command -v docker >/dev/null || warn "没有 docker 命令，只能删文件"
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  step "1/4 停容器、删 compose 项目"
  if [ -f "$COMPOSE" ]; then
    docker compose -f "$COMPOSE" --project-name "$PROJECT" down --remove-orphans 2>&1 | sed 's/^/  /' || warn "compose down 没成功"
  else
    docker rm -f "$CONTAINER" 2>/dev/null | sed 's/^/  /' || true
  fi
  step "2/4 删镜像"
  docker rmi "$IMAGE" 2>&1 | sed 's/^/  /' || warn "镜像没删掉（可能被别的容器用着）"
else
  warn "跳过 docker 操作（当前用户用不了 docker）"
fi
step "3/4 删程序"
rm -rf "$PREFIX/app" "$PREFIX/run"
rm -f "$COMPOSE"
info "已删 $PREFIX/app、compose.yaml"
step "4/4 结果"
if [ "$PURGE" = "1" ]; then
  [ -n "$PREFIX" ] && [ "$PREFIX" != "/" ] || die "拒绝删除 $PREFIX"
  rm -rf "$PREFIX"
  info "已删除整个 ${PREFIX}（含容器数据目录）"
else
  cat <<EOF
  已卸载，留下的东西：
    数据    $PREFIX/data/magnet_items.db
    日志    $PREFIX/logs（容器日志在 docker logs，重建后用同名容器继续看）
    配置    $PREFIX/config/.env（600）· $PREFIX/api-key.txt
  重装会继续用同一个 API Key 和同一份资源库。
  要彻底删干净：bash $PREFIX/uninstall.sh --purge --yes
EOF
fi
printf '\n完成。\n'
