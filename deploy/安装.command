#!/bin/bash
# ============================================================================
# 双击安装：把 qb-nas 中心服务装到 NAS 上（Docker 容器，数据和镜像都在存储空间 3）
#
#   Mac 上只需要自带 ssh；需要 NAS 上 llll 能用 docker（见文末提示）。
#   代码从当前这份工作副本 rsync 到 NAS（不再依赖 NAS 上的 git 仓库）。
# ============================================================================
set -uo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR" || exit 1
pause() { if [ -t 0 ]; then printf '\n按回车关闭窗口…'; read -r _; fi; }
NAS_USER=${QB_NAS_USER:-llll}
NAS_HOST=${QB_NAS_HOST:-192.168.20.193}
PREFIX=${QB_NAS_PREFIX:-/vol3/1000/qb-nas}
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 "$NAS_USER@$NAS_HOST")
banner() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; pause; exit 1; }

banner "1/3 连 NAS（$NAS_USER@${NAS_HOST}）"
if ! "${SSH[@]}" true 2>/dev/null; then
  cat <<EOF
连不上 NAS。常见原因：
  · NAS 关机/睡眠，或不在同一个局域网
  · 这台 Mac 的 SSH 公钥没装到 NAS：ssh-copy-id $NAS_USER@$NAS_HOST
  · 地址不对：QB_NAS_HOST=192.168.x.x 重新运行
EOF
  pause; exit 1
fi
printf '  NAS 在线\n'

banner "2/3 安装到 NAS（Docker，首次 3–8 分钟）"
DOCKER_OK=$("${SSH[@]}" 'docker info >/dev/null 2>&1 && echo yes || echo no')
if [ "$DOCKER_OK" != "yes" ]; then
  printf '\n\033[33m! llll 现在用不了 docker（NAS 的 docker 组里没有这个用户）。\033[0m\n'
  printf '  先做一次（二选一，之后就不用再管）：\n\n'
  printf '  A. 在 NAS 终端执行：sudo usermod -aG docker llll   然后重连 SSH\n'
  printf '  B. fnOS 网页 → 用户与群组 → llll → 编辑 → 用户组勾选 docker → 保存 → 重连 SSH\n\n'
  printf '  不想动权限：用 deploy/打包-导入包.sh 生成导入包，在 fnOS Docker 界面导入。\n'
  pause; exit 3
fi
printf '  docker 可用\n'

SRC=${QB_NAS_SRC:-$(git -C "$DIR/.." rev-parse --show-toplevel 2>/dev/null || echo "$DIR/..")}
COMMIT=$(git -C "$SRC" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)
printf '  源码：%s\n  构建标记：%s\n' "$SRC" "$COMMIT"
"${SSH[@]}" "mkdir -p /vol3/1000/qb-nas-bootstrap/src" || fail "NAS 上建临时目录失败"
RSYNC=(rsync -a --delete --exclude '.git/' --exclude '.venv/' --exclude 'node_modules/' --exclude '__pycache__/' --exclude '.pytest_cache/' --exclude '.ruff_cache/' --exclude 'tests/' --exclude 'docs/' --exclude '.env' --exclude '.env.*')
"${RSYNC[@]}" "$SRC/" "$NAS_USER@$NAS_HOST:/vol3/1000/qb-nas-bootstrap/src/" || fail "传代码失败"
rsync -a "./" "$NAS_USER@$NAS_HOST:/vol3/1000/qb-nas-bootstrap/src/deploy/" || fail "传安装脚本失败"
"${SSH[@]}" "bash /vol3/1000/qb-nas-bootstrap/src/deploy/install.sh --source /vol3/1000/qb-nas-bootstrap/src --commit '$COMMIT' --prefix '$PREFIX'" 2>&1
rc=$?
[ "${rc:-1}" -eq 0 ] || fail "安装没成功，上面是要排查的输出（NAS 上：docker logs $PREFIX 对应容器名；构建日志 $PREFIX/run/build.log）"

"${SSH[@]}" "rm -rf /vol3/1000/qb-nas-bootstrap" >/dev/null 2>&1 || true
banner "3/3 完成"
KEY=$("${SSH[@]}" "cat '$PREFIX/api-key.txt'" 2>/dev/null)
PORT=8899
cat <<EOF
  Web UI     http://$NAS_HOST:$PORT/
  健康检查   http://$NAS_HOST:$PORT/api/health
  API Key    ${KEY:-见 NAS 上 $PREFIX/api-key.txt}

  接下来（Mac 菜单栏代理第一次运行时）：
    1. NAS 地址填 http://$NAS_HOST:$PORT
    2. API Key 粘贴上面这一串
  容器 restart=unless-stopped：NAS 重启、进程崩了都会自己回来。
EOF
pause
