#!/bin/bash
# 双击看一眼：容器状态、健康检查、最近日志
set -uo pipefail
cd "$(dirname "$0")" || exit 1
pause() { if [ -t 0 ]; then printf '\n按回车关闭窗口…'; read -r _; fi; }
NAS_USER=${QB_NAS_USER:-llll}; NAS_HOST=${QB_NAS_HOST:-192.168.20.193}
CONTAINER=${QB_NAS_CONTAINER:-qb-nas-center}; PORT=${QB_NAS_PORT:-8899}
ssh -o BatchMode=yes -o ConnectTimeout=8 "$NAS_USER@$NAS_HOST" "
  echo '--- 容器 ---'
  docker ps -a --filter name='$CONTAINER' --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
  echo '--- 健康 ---'
  curl -fsS --max-time 5 http://127.0.0.1:$PORT/api/health || echo '（无响应）'
  echo
  echo '--- 最近日志 ---'
  docker logs --tail 40 '$CONTAINER' 2>&1 | tail -40
" 2>&1
pause
