#!/bin/bash
# ============================================================================
# 双击安装：把 qb-nas 中心服务装到 NAS 上（Docker 容器，数据和镜像都在存储空间 3）
#
#   Mac 上只需要自带 ssh；需要 NAS 上 llll 能用 docker（见文末提示）。
#   调试时可用 QB_NAS_SRC=/本机/代码目录 改用本机代码；默认取 NAS 仓库里的 ref。
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")" || exit 1
pause() { if [ -t 0 ]; then printf '\n按回车关闭窗口…'; read -r _; fi; }
NAS_USER=${QB_NAS_USER:-llll}
NAS_HOST=${QB_NAS_HOST:-192.168.20.193}
REPO=${QB_NAS_REPO:-/vol1/1000/github/qb-nas}
PREFIX=${QB_NAS_PREFIX:-/vol3/1000/qb-nas}
REF=${QB_NAS_REF:-stage/resident}
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 "$NAS_USER@$NAS_HOST")
banner() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; pause; exit 1; }

banner "1/3 连 NAS（$NAS_USER@$NAS_HOST）"
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

if [ -n "${QB_NAS_SRC:-}" ]; then
  printf '  用本机代码：%s\n' "$QB_NAS_SRC"
  "${SSH[@]}" "mkdir -p /tmp/qb-nas-src" || fail "NAS 上建临时目录失败"
  RSYNC=(rsync -a --delete --exclude '.git/' --exclude '.venv/' --exclude 'node_modules/' --exclude '__pycache__/' --exclude '.pytest_cache/' --exclude 'tests/' --exclude 'docs/' --exclude 'doc/' --exclude '.env' --exclude '.env.*')
  "${RSYNC[@]}" "$QB_NAS_SRC/" "$NAS_USER@$NAS_HOST:/tmp/qb-nas-src/" || fail "传代码失败"
  rsync -a "./" "$NAS_USER@$NAS_HOST:/tmp/qb-nas-src/deploy/" || fail "传安装脚本失败"
  "${SSH[@]}" "bash /tmp/qb-nas-src/deploy/install.sh --source /tmp/qb-nas-src --prefix '$PREFIX'" 2>&1
  rc=$?
else
  "${SSH[@]}" "set -e
    tmp=\$(mktemp -d /tmp/qb-nas-install.XXXXXX)
    ref='$REF'
    if ! git -C '$REPO' rev-parse --verify --quiet \"\$ref^{commit}\" >/dev/null; then
      echo '  仓库里没有 $REF，退到当前分支'; git -C '$REPO' branch --format='%(refname:short)' | sed 's/^/    /'
      ref=\$(git -C '$REPO' symbolic-ref --short -q HEAD || echo main)
    fi
    git -C '$REPO' archive \"\$ref\" deploy | tar -x -C \"\$tmp\"
    bash \"\$tmp/deploy/install.sh\" --git-ref \"\$ref\" --git-repo '$REPO' --prefix '$PREFIX'
  " 2>&1
  rc=$?
fi
[ "${rc:-1}" -eq 0 ] || fail "安装没成功，上面是要排查的输出（NAS 上：docker logs $PREFIX 对应容器名；构建日志 $PREFIX/run/build.log）"

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
