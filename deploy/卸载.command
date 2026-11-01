#!/bin/bash
# 双击卸载：停容器、删镜像和程序；数据/日志/API Key 保留
set -uo pipefail
cd "$(dirname "$0")" || exit 1
pause() { if [ -t 0 ]; then printf '\n按回车关闭窗口…'; read -r _; fi; }
NAS_USER=${QB_NAS_USER:-llll}; NAS_HOST=${QB_NAS_HOST:-192.168.20.193}
PREFIX=${QB_NAS_PREFIX:-/vol3/1000/qb-nas}
printf '\n== 卸载 qb-nas 中心服务（保留数据）==\n'
ssh -o BatchMode=yes -o ConnectTimeout=8 "$NAS_USER@$NAS_HOST" \
  "bash '$PREFIX/uninstall.sh' --prefix '$PREFIX'" 2>&1 \
  || { printf '\n\033[31m✗ 卸载失败：NAS 连不上，或 $PREFIX/uninstall.sh 不存在\033[0m\n' >&2; pause; exit 1; }
pause
