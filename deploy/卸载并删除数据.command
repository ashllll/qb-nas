#!/bin/bash
# 双击彻底卸载：容器、镜像、程序、资源库、Key 全删（二次确认）
set -uo pipefail
cd "$(dirname "$0")" || exit 1
pause() { if [ -t 0 ]; then printf '\n按回车关闭窗口…'; read -r _; fi; }
NAS_USER=${QB_NAS_USER:-llll}; NAS_HOST=${QB_NAS_HOST:-192.168.20.193}
PREFIX=${QB_NAS_PREFIX:-/vol3/1000/qb-nas}
printf '\n\033[33m这会删掉 NAS 上的资源库（%s/data）、日志和 API Key。\033[0m\n' "$PREFIX"
printf '输入 删除 再回车确认：'
read -r ans
[ "$ans" = "删除" ] || { printf '已取消。\n'; pause; exit 1; }
ssh -o BatchMode=yes -o ConnectTimeout=8 "$NAS_USER@$NAS_HOST" \
  "bash '$PREFIX/uninstall.sh' --prefix '$PREFIX' --purge --yes" 2>&1 \
  || { printf '\n\033[31m✗ 卸载失败：NAS 连不上，或 $PREFIX/uninstall.sh 不存在\033[0m\n' >&2; pause; exit 1; }
pause
