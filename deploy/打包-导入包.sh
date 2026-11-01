#!/usr/bin/env bash
# ============================================================================
# 生成「给 NAS Docker 界面导入」的压缩包（没有 docker 权限时的备用路径）
#
#   在 Mac 上：bash deploy/打包-导入包.sh          → 产出 deploy/导入包/qb-nas-import-<日期>.tar.gz
#   在 NAS 上：bash deploy/install.sh --no-start 也可先把代码落到 /vol3，再在界面里用
#
#   包里：代码（构建上下文）、Dockerfile、.dockerignore、compose.yaml（无 build 版）、
#         .env.template、导入说明.md；本机有 docker 时额外 docker save 镜像 tar。
# ============================================================================
set -Eeuo pipefail
SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
APP_ROOT=$(cd "$SELF_DIR/.." && pwd)
OUT="${QB_NAS_PACK_OUT:-$SELF_DIR/导入包}"
STAMP=$(date +%Y%m%d-%H%M)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/qb-nas-import.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
B="$WORK/qb-nas"
mkdir -p "$B/app" "$B/data" "$B/logs" "$B/backup"

echo "▶ 收代码"
rsync -a --exclude '.git/' --exclude '.venv' --exclude 'node_modules/' --exclude '__pycache__/' \
  --exclude '.pytest_cache/' --exclude '.ruff_cache/' --exclude '.blueprint-tmp/' --exclude 'tests/' \
  --exclude 'mac_agent/' --exclude 'scripts/' --exclude 'docs/' --exclude 'doc/' --exclude '.env' \
  --exclude '.env.*' --exclude 'data/' --exclude 'logs/' --exclude '.DS_Store' --exclude '导出/' \
  --exclude '.husky/' --exclude '.claude/' --exclude '.codegraph/' --exclude '.context/' \
  --exclude '.codebase-memory/' --exclude 'package.json' --exclude 'package-lock.json' \
  "$APP_ROOT/" "$B/app/"
cp "$SELF_DIR/docker/Dockerfile" "$B/app/Dockerfile"
cp "$SELF_DIR/docker/dockerignore" "$B/app/.dockerignore"

echo "▶ 生成 compose（无 build 版，镜像用导入的 tar）"
sed -e 's|@@PROJECT@@|qb-nas|g' \
    -e 's|@@APP@@|/vol3/1000/qb-nas/app|g' \
    -e 's|@@IMAGE@@|qb-nas-center:local|g' \
    -e 's|@@CONTAINER@@|qb-nas-center|g' \
    -e 's|@@ENV_FILE@@|/vol3/1000/qb-nas/.env|g' \
    -e 's|@@HOST_PORT@@|8899|g' \
    -e 's|@@DATA_DIR@@|/vol3/1000/qb-nas/data|g' \
    -e 's|@@LOG_DIR@@|/vol3/1000/qb-nas/logs|g' \
    -e 's|@@PYPI_INDEX@@||g' \
    -e 's|@@WITH_BROWSER@@|0|g' \
    "$SELF_DIR/compose.yaml.tpl" | awk '
      /^    build:/ {skip=1}
      skip && /^    image:/ {skip=0}
      !skip {print}
    ' > "$B/compose.yaml"

cp "$SELF_DIR/docker/compose.nobuild.yaml" "$B/compose.nobuild.yaml"

cat > "$B/.env.template" <<'ENVT'
SERVICE_HOST=0.0.0.0
SERVICE_PORT=8899
API_KEY=在这里填一个随机串（至少 24 位，例如 openssl rand -hex 24 的结果）
STORE_BACKEND=sqlite
STORE_DB_PATH=/data/magnet_items.db
CRAWLER_HTTP_FIRST=true
CRAWLER_ALLOWED_RESOLUTIONS=
CRAWLER_MAX_DEPTH=2
CRAWLER_TIMEOUT=30
CRAWLER_CONCURRENCY=4
LOG_FILE=
LOG_LEVEL=INFO
QBIT_HOST=
QBIT_USERNAME=
QBIT_PASSWORD=
ENVT

cat > "$B/导入说明.md" <<'DOC'
# 在 NAS 的 Docker 界面导入（无 SSH/docker 权限时）

1. 把这个目录整个放到 NAS 的 **存储空间 3**：
   `/vol3/1000/qb-nas/`（`app/`、`data/`、`logs/`、`compose.yaml`、`.env` 都在这一层）。
2. 复制 `.env.template` 为 `.env`，把 `API_KEY=` 填成一个随机串（24 位以上）。
3. 打开 fnOS 的 Docker 管理界面：
   - 有 `qb-nas-center-*.tar`：先在「镜像 → 导入」里导入它；
   - 「项目 / Compose」→ 新建项目，名称 `qb-nas`，粘贴 `compose.yaml` 内容；
   - 若界面不支持 `build`：确认 `image:` 指向已导入的镜像，删掉 `build:` 段；
   - 端口 `8899:8899`，重启策略 `unless-stopped`，数据目录 `/vol3/1000/qb-nas/data`。
4. 启动后访问 `http://<NAS>:8899/api/health`，返回 `{"ok":true,...}` 就成。
5. API Key 在 `.env` 里；Mac 代理第一次配置时填 `http://<NAS>:8899` + 这串 Key。
DOC

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "▶ 构建并导出镜像（本机 docker 可用）"
  docker build \
    --build-arg "PYPI_INDEX=${QB_NAS_PYPI_INDEX:-https://mirrors.aliyun.com/pypi/simple/}" \
    -t qb-nas-center:local "$B/app"
  docker save qb-nas-center:local -o "$B/qb-nas-center-image.tar"
else
  echo "  （本机没有可用的 docker，跳过镜像导出：请在 NAS Docker 界面用 compose + 构建，或先加 docker 组）"
fi

mkdir -p "$OUT"
TAR="$OUT/qb-nas-import-$STAMP.tar.gz"
tar -C "$WORK" -czf "$TAR" qb-nas
echo
echo "✓ 导入包：$TAR"
ls -lh "$TAR"
