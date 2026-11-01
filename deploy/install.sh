#!/usr/bin/env bash
# ============================================================================
# qb-nas 中心服务 · NAS 容器化安装（Docker / docker compose）
#
#   在 NAS 上以 llll 运行（需要能用 docker；见下面「没有 docker 权限怎么办」）。
#   · 代码 → <prefix>/app（构建上下文）
#   · 数据 → <prefix>/data，日志走 docker logs；两者都在 /vol3（存储空间 3）
#   · 容器 restart=unless-stopped：NAS 重启自己回来，崩了 docker 拉起
#   · 生成 <prefix>/.env（600，含随机 API Key）与 <prefix>/compose.yaml
#
#   仓库里直接跑：bash deploy/install.sh            （源码 = 本仓库）
#   测试档：      bash deploy/install.sh --test     （/vol3/1000/qb-nas-test + 18999）
#   指定 ref：    bash deploy/install.sh --git-ref stage/resident
#
#   没有 docker 权限怎么办（任选其一，装完即用）：
#     sudo usermod -aG docker llll     # 然后重新登录 SSH（新会话才带新组）
#     fnOS 网页 → 用户与群组 → llll → 编辑 → 用户组勾选 docker → 保存 → 重新登录
#   两条都不想做：把 compose.yaml + 镜像 tar 交给用户在 fnOS Docker 界面导入（见 README）。
# ============================================================================
set -Eeuo pipefail

SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# ── 默认值（生产）────────────────────────────────────────────────────────────
PREFIX=/vol3/1000/qb-nas
HOST_PORT=8899
PROJECT=qb-nas
CONTAINER=qb-nas-center
IMAGE=qb-nas-center:local
MODE_NAME=production
SOURCE=""
GIT_REF=""
GIT_REPO=/vol1/1000/github/qb-nas
API_KEY=""
ROTATE_KEY=0
START=1
WITH_BROWSER=0
PYPI_INDEX=""
QBIT_HOST=""
QBIT_USER=""
QBIT_PASS=""
ALLOW_ANY_DISK=0
LOG_LEVEL=INFO

c_ok=$'\033[32m'; c_warn=$'\033[33m'; c_err=$'\033[31m'; c_dim=$'\033[2m'; c_0=$'\033[0m'
if [ ! -t 1 ]; then c_ok=""; c_warn=""; c_err=""; c_dim=""; c_0=""; fi
step() { printf '\n%s▶ %s%s\n' "$c_ok" "$*" "$c_0"; }
info() { printf '  %s\n' "$*"; }
dim()  { printf '  %s%s%s\n' "$c_dim" "$*" "$c_0"; }
warn() { printf '  %s! %s%s\n' "$c_warn" "$*" "$c_0" >&2; }
die()  { printf '\n%s✗ %s%s\n' "$c_err" "$*" "$c_0" >&2; exit 1; }
trap 'die "第 $LINENO 行失败（退出码 $?）。上面的输出是原因。"' ERR

lan_ip() { ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1; }

usage() {
  sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<'USAGE'

参数：
  --prefix DIR        安装根目录（默认 /vol3/1000/qb-nas；必须在 /vol3 下）
  --port N            对外端口（默认 8899，容器内固定 8899）
  --project NAME      compose 项目名（默认 qb-nas）
  --container NAME    容器名（默认 qb-nas-center）
  --image TAG         镜像标签（默认 qb-nas-center:local）
  --source DIR        用这个目录的代码（默认=本脚本所在仓库）
  --git-ref REF       从 --git-repo 的 git 对象取代码
  --git-repo DIR      NAS 上的代码仓库（默认 /vol1/1000/github/qb-nas）
  --api-key KEY       指定 API Key（默认首次随机生成，之后保留）
  --rotate-key        重新生成 API Key
  --pypi-index URL    构建时用的 PyPI 源（默认自动探测阿里云镜像）
  --qbit-host/-user/-pass   顺带写入 qB 配置（可之后在 Web UI 填）
  --with-browser      镜像内装 Chromium（HTTP 优先，通常不需要）
  --test              测试档：prefix=/vol3/1000/qb-nas-test，port=18999，名字都带 -test
  --allow-any-disk    允许装到 /vol3 以外（默认拒绝，用户要求数据只在存储空间 3）
  --no-start          只准备、不构建不启动
  -h, --help          这份说明
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX=${2:?}; shift 2 ;;
    --port) HOST_PORT=${2:?}; shift 2 ;;
    --project) PROJECT=${2:?}; shift 2 ;;
    --container) CONTAINER=${2:?}; shift 2 ;;
    --image) IMAGE=${2:?}; shift 2 ;;
    --source) SOURCE=${2:?}; shift 2 ;;
    --git-ref) GIT_REF=${2:?}; shift 2 ;;
    --git-repo) GIT_REPO=${2:?}; shift 2 ;;
    --api-key) API_KEY=${2:?}; shift 2 ;;
    --rotate-key) ROTATE_KEY=1; shift ;;
    --pypi-index) PYPI_INDEX=${2:?}; shift 2 ;;
    --qbit-host) QBIT_HOST=${2:?}; shift 2 ;;
    --qbit-user) QBIT_USER=${2:?}; shift 2 ;;
    --qbit-pass) QBIT_PASS=${2:?}; shift 2 ;;
    --with-browser) WITH_BROWSER=1; shift ;;
    --allow-any-disk) ALLOW_ANY_DISK=1; shift ;;
    --test) PREFIX=/vol3/1000/qb-nas-test; HOST_PORT=18999; PROJECT=qb-nas-test; \
            CONTAINER=qb-nas-center-test; IMAGE=qb-nas-center:test; MODE_NAME=test; shift ;;
    --no-start) START=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "不认识的参数：$1（--help 看用法）" ;;
  esac
done

APP="$PREFIX/app"
DATA="$PREFIX/data"
LOGS="$PREFIX/logs"
RUN="$PREFIX/run"
ENV_FILE="$PREFIX/.env"
COMPOSE="$PREFIX/compose.yaml"
URL_HOST=127.0.0.1

printf '%s\n' "${c_dim}qb-nas 中心服务 · 容器化安装（$MODE_NAME）${c_0}"
dim "根目录 $PREFIX（数据只在 /vol3）"
dim "端口   $HOST_PORT → 容器 8899"
dim "项目   $PROJECT · 容器 $CONTAINER · 镜像 $IMAGE"

# ── 0. 现场检查 ──────────────────────────────────────────────────────────────
step "0/8 检查现场"
[ "$(id -u)" -ne 0 ] || die "请用普通用户（llll）运行，不要用 root"
command -v docker >/dev/null || die "没有 docker 命令"
command -v curl >/dev/null || die "没有 curl"
case "$PREFIX" in
  /vol3/*) : ;;
  *) [ "$ALLOW_ANY_DISK" = "1" ] || die "安装目录必须在 /vol3（存储空间 3）下；确有需要加 --allow-any-disk" ;;
esac
[ -d /vol3 ] || die "没有 /vol3"
mkdir -p "$PREFIX" 2>/dev/null || die "/vol3 下不可写：$PREFIX"
[ -w "$PREFIX" ] || die "/vol3 下不可写：$PREFIX"
FREE_GB=$(df -Pk "$PREFIX" | awk 'NR==2{printf "%d", $4/1048576}')
[ "$FREE_GB" -ge 4 ] || die "存储空间 3 剩余 ${FREE_GB}GB，不够（镜像 + 数据至少 4GB）"
info "存储空间 3 剩余 ${FREE_GB}GB · $(df -Pk "$PREFIX" | awk 'NR==2{print $6}')"

if ! docker info >/dev/null 2>&1; then
  printf '\n%s✗ 当前用户 llll 用不了 docker（socket 属 root:docker，660）%s\n' "$c_err" "$c_0" >&2
  cat >&2 <<EOF

  这一条必须你来给（二选一，都是一次性的）：

  A. NAS 终端里执行（会问你的 NAS 密码）：
       sudo usermod -aG docker llll
     然后断开重连 SSH（当前会话不带新组）。

  B. fnOS 网页 → 用户与群组 → 选中 llll → 编辑 → 用户组勾选 docker → 保存，
     然后重新登录 SSH。

  做完重跑同一个安装命令即可。不想动权限也行：
     用 deploy/打包-导入包.sh 生成导入包，在 fnOS Docker 界面导入（步骤见 README）。
EOF
  exit 3
fi
DOCKER_VER=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?')
COMPOSE_VER=$(docker compose version --short 2>/dev/null || echo '?')
info "docker $DOCKER_VER · compose $COMPOSE_VER"
info "docker 根：$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo '?')"
ROOT_DIR=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo "")
case "$ROOT_DIR" in
  /vol3/*) : ;;
  "") warn "读不到 docker 根目录" ;;
  *) warn "docker 根在 $ROOT_DIR（不在 /vol3），镜像层可能落在别的存储空间" ;;
esac

# 端口占用（同端口被别的容器/进程占了先停）
if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$HOST_PORT\$"; then
  if docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -q "$CONTAINER"; then
    info "端口 $HOST_PORT 由本服务旧容器占用，稍后会重建"
  else
    die "端口 $HOST_PORT 已被占用：$(ss -ltnp 2>/dev/null | grep -E "[:.]$HOST_PORT\b" || true)"
  fi
fi

# ── 1. 取代码（先落临时目录，验完再换）──────────────────────────────────────
step "1/8 取代码"
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/qb-nas-stage.XXXXXX")
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT
COMMIT=local
if [ -n "$GIT_REF" ]; then
  command -v git >/dev/null || die "没有 git"
  [ -d "$GIT_REPO/.git" ] || die "不是 git 仓库：$GIT_REPO"
  git -C "$GIT_REPO" rev-parse --verify --quiet "$GIT_REF^{commit}" >/dev/null \
    || die "仓库 $GIT_REPO 里没有这个 ref：$GIT_REF"
  COMMIT=$(git -C "$GIT_REPO" rev-parse --short "$GIT_REF")
  tar -C "$STAGE" -xf <(git -C "$GIT_REPO" archive "$GIT_REF")
  info "来源：$GIT_REPO @ $GIT_REF（$COMMIT）"
else
  if [ -z "$SOURCE" ]; then
    if [ -f "$SELF_DIR/../magnet_harvester/main.py" ]; then SOURCE=$(cd "$SELF_DIR/.." && pwd)
    else die "没有代码来源：给 --source DIR 或 --git-ref REF"; fi
  fi
  [ -f "$SOURCE/magnet_harvester/main.py" ] || die "$SOURCE 里没有 magnet_harvester/main.py"
  command -v rsync >/dev/null || die "没有 rsync"
  SRC_RO=$(cd "$SOURCE" && pwd)
  rsync -a --exclude '.git/' --exclude '.venv/' --exclude 'node_modules/' --exclude '__pycache__/' \
    --exclude '.pytest_cache/' --exclude '.ruff_cache/' --exclude 'tests/' --exclude '.env' --exclude '.env.*' \
    --exclude '*.log' --exclude 'logs/' --exclude 'data/' --exclude 'docs/' --exclude 'doc/' \
    --exclude '.husky/' --exclude '.claude/' --exclude '.codegraph/' --exclude '.context/' \
    --exclude '.codebase-memory/' --exclude '.DS_Store' "$SRC_RO/" "$STAGE/"
  info "来源：本地目录 $SRC_RO"
fi
[ -f "$STAGE/magnet_harvester/main.py" ] || die "代码里没有 magnet_harvester/main.py"
[ -f "$STAGE/requirements.txt" ] || die "代码里没有 requirements.txt"

# ── 2. 落地目录与代码 ────────────────────────────────────────────────────────
step "2/8 落地代码与目录"
mkdir -p "$APP" "$DATA" "$LOGS" "$RUN" "$PREFIX/backup"
chmod 700 "$DATA" "$RUN"; chmod 750 "$LOGS"
cp -p "$SELF_DIR/docker/Dockerfile" "$APP/Dockerfile"
cp -p "$SELF_DIR/docker/dockerignore" "$APP/.dockerignore"
for helper in uninstall.sh verify.sh; do
  [ -f "$SELF_DIR/$helper" ] && install -m 0755 "$SELF_DIR/$helper" "$PREFIX/$helper"
done
rsync -a --delete --exclude 'Dockerfile' --exclude '.dockerignore' \
  --exclude 'data/' --exclude 'logs/' --exclude '.env' "$STAGE/" "$APP/"
info "构建上下文 $APP（$COMMIT）"

# ── 3. 配置 .env（600）───────────────────────────────────────────────────────
step "3/8 写 .env（600）"
OLD_KEY=""
[ -f "$ENV_FILE" ] && OLD_KEY=$(grep -E '^API_KEY=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)
[ -n "$API_KEY" ] || API_KEY="$OLD_KEY"
if [ -z "$API_KEY" ] || [ "$ROTATE_KEY" = "1" ]; then
  API_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(24))')
  [ "$ROTATE_KEY" = "1" ] && info "已按要求重新生成 API Key"
fi
[ ${#API_KEY} -ge 24 ] || die "API Key 太短（至少 24 字符）"
if [ -z "$QBIT_HOST" ] && [ -f "$ENV_FILE" ]; then
  QBIT_HOST=$(grep -E '^QBIT_HOST=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)
  QBIT_USER=$(grep -E '^QBIT_USERNAME=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)
  QBIT_PASS=$(grep -E '^QBIT_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)
fi
[ -f "$ENV_FILE" ] && cp -p "$ENV_FILE" "$PREFIX/backup/env-$(date +%Y%m%d-%H%M%S).bak" && chmod 600 "$PREFIX/backup"/env-*.bak 2>/dev/null || true
ls -1t "$PREFIX/backup"/env-*.bak 2>/dev/null | tail -n +4 | xargs -r rm -f || true
umask 077
cat > "$ENV_FILE" <<ENV
# qb-nas 中心服务 · 容器运行配置（$MODE_NAME）· 由 deploy/install.sh 生成
# 权限 600；不要进仓库。改完执行：docker compose -f $COMPOSE --project-name $PROJECT up -d --force-recreate
SERVICE_HOST=0.0.0.0
SERVICE_PORT=8899
API_KEY=$API_KEY

# 存储：容器内 /data ← $DATA（/vol3，持久）
STORE_BACKEND=sqlite
STORE_DB_PATH=/data/magnet_items.db

# 取页：公开页 HTTP 优先；1080p/720p 也收
CRAWLER_HTTP_FIRST=true
CRAWLER_ALLOWED_RESOLUTIONS=
CRAWLER_MAX_DEPTH=2
CRAWLER_TIMEOUT=30
CRAWLER_CONCURRENCY=4

# 日志：统一 stdout → docker logs（compose 里 20MB×5 轮转）
LOG_FILE=
LOG_LEVEL=$LOG_LEVEL

# qBittorrent（可空：只影响“下载”按钮，不影响入库）
QBIT_HOST=$QBIT_HOST
QBIT_USERNAME=$QBIT_USER
QBIT_PASSWORD=$QBIT_PASS
ENV
umask 022
chmod 600 "$ENV_FILE"
printf '%s\n' "$API_KEY" > "$PREFIX/api-key.txt"; chmod 600 "$PREFIX/api-key.txt"
info "配置 $ENV_FILE · Key 存 $PREFIX/api-key.txt"

# ── 4. compose.yaml ──────────────────────────────────────────────────────────
step "4/8 生成 compose.yaml"
[ -n "$PYPI_INDEX" ] || PYPI_INDEX=${QB_NAS_PYPI_INDEX:-}
if [ -z "$PYPI_INDEX" ]; then
  if curl -fsS --max-time 4 -o /dev/null https://mirrors.aliyun.com/pypi/simple/ 2>/dev/null; then
    PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple/
  fi
fi
[ -n "$PYPI_INDEX" ] && dim "构建源 $PYPI_INDEX"
sed -e "s|@@PROJECT@@|$PROJECT|g" \
    -e "s|@@APP@@|$APP|g" \
    -e "s|@@IMAGE@@|$IMAGE|g" \
    -e "s|@@CONTAINER@@|$CONTAINER|g" \
    -e "s|@@ENV_FILE@@|$ENV_FILE|g" \
    -e "s|@@HOST_PORT@@|$HOST_PORT|g" \
    -e "s|@@DATA_DIR@@|$DATA|g" \
    -e "s|@@LOG_DIR@@|$LOGS|g" \
    -e "s|@@PYPI_INDEX@@|$PYPI_INDEX|g" \
    -e "s|@@WITH_BROWSER@@|$WITH_BROWSER|g" \
    "$SELF_DIR/compose.yaml.tpl" > "$COMPOSE"
chmod 640 "$COMPOSE"
docker compose -f "$COMPOSE" --project-name "$PROJECT" config >/dev/null \
  || die "生成的 compose.yaml 不合法（$COMPOSE）"
info "$COMPOSE"

# ── 5. 构建 + 启动 ──────────────────────────────────────────────────────────
step "5/8 构建镜像并启动（首次 3–8 分钟，取决于拉基础镜像）"
if [ "$START" = "1" ]; then
  BUILD_LOG="$RUN/build.log"
  if docker compose -f "$COMPOSE" --project-name "$PROJECT" build --progress plain >"$BUILD_LOG" 2>&1; then
    tail -n 6 "$BUILD_LOG" | sed 's/^/  /'
  else
    tail -n 40 "$BUILD_LOG" | sed 's/^/  /'
    die "镜像构建失败，完整日志 $BUILD_LOG"
  fi
  docker compose -f "$COMPOSE" --project-name "$PROJECT" up -d --remove-orphans 2>&1 | sed 's/^/  /'
else
  info "--no-start：只准备好，不构建"
fi

# ── 6. 健康检查 ─────────────────────────────────────────────────────────────
step "6/8 健康检查"
HEALTH_URL="http://$URL_HOST:$HOST_PORT/api/health"
if [ "$START" = "1" ]; then
  ok=0
  for i in $(seq 1 90); do
    if curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1 \
      || curl -fsS --max-time 3 "http://$URL_HOST:$HOST_PORT/api/v1/health" >/dev/null 2>&1; then
      ok=1; break
    fi
    st=$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo missing)
    [ "$st" = "exited" ] || [ "$st" = "missing" ] && break
    sleep 1
  done
  if [ "$ok" != "1" ]; then
    printf '\n%s✗ 容器没起来。最近日志：%s\n' "$c_err" "$c_0" >&2
    docker logs --tail 80 "$CONTAINER" 2>&1 | sed 's/^/  /' >&2 || true
    die "排查：docker logs $CONTAINER；构建日志 $RUN/build.log"
  fi
  info "健康检查通过：$HEALTH_URL"
  HC=$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo unknown)
  info "容器 $CONTAINER · $(docker inspect -f '{{.State.Status}}' "$CONTAINER") · health=$HC"
else
  info "跳过（--no-start）"
fi

# ── 7. 结果 ─────────────────────────────────────────────────────────────────
step "7/8 结果"
LAN=$(lan_ip)
IS_LIVE=$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo 'not-running')
RESTART_POLICY=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$CONTAINER" 2>/dev/null || echo '?')
DB_SIZE=$(du -h "$DATA/magnet_items.db" 2>/dev/null | cut -f1 || true)
cat > "$PREFIX/部署信息.txt" <<INFO_EOF
qb-nas 中心服务 · 部署信息（$MODE_NAME · $(date '+%Y-%m-%d %H:%M:%S')）
------------------------------------------------------------
Web UI    http://$LAN:$HOST_PORT/
健康      http://$LAN:$HOST_PORT/api/health
API Key   见 $PREFIX/api-key.txt（600），Mac 代理第一次配置时粘贴
根目录    $PREFIX（存储空间 3 /vol3）
代码      $APP（$COMMIT）
数据      $DATA/magnet_items.db（容器内 /data）
镜像      $IMAGE（层在 /vol3/docker）
容器      $CONTAINER（restart=$RESTART_POLICY）
日志      docker logs -f $CONTAINER（20MB×5 轮转）
compose   $COMPOSE

常用命令（在 NAS 上）：
  docker ps --filter name=$CONTAINER         # 看容器
  docker logs --tail 100 -f $CONTAINER       # 跟日志
  docker compose -f $COMPOSE --project-name $PROJECT restart
  bash $PREFIX/uninstall.sh                  # 卸载（保留数据）
  bash $PREFIX/uninstall.sh --purge --yes    # 卸载并删数据

以后更新代码：重跑 deploy/install.sh（幂等，保留 Key 和数据）
INFO_EOF
chmod 600 "$PREFIX/部署信息.txt"

printf '\n%s┌────────────────────────────────────────────────────────────┐%s\n' "$c_ok" "$c_0"
printf '%s│%s 装好了：%s\n' "$c_ok" "$c_0" "$MODE_NAME"
printf '%s│%s 地址    http://%s:%s/\n' "$c_ok" "$c_0" "$LAN" "$HOST_PORT"
printf '%s│%s 健康    %s\n' "$c_ok" "$c_0" "$HEALTH_URL"
printf '%s│%s 容器    %s（status=%s · restart=%s）\n' "$c_ok" "$c_0" "$CONTAINER" "$IS_LIVE" "$RESTART_POLICY"
printf '%s│%s 数据    %s/magnet_items.db%s\n' "$c_ok" "$c_0" "$DATA" "${DB_SIZE:+（$DB_SIZE）}"
printf '%s│%s 日志    docker logs -f %s\n' "$c_ok" "$c_0" "$CONTAINER"
printf '%s│%s Key     %s/api-key.txt（600）\n' "$c_ok" "$c_0" "$PREFIX"
printf '%s└────────────────────────────────────────────────────────────┘%s\n' "$c_ok" "$c_0"
printf '\n  %s这次生成的 API Key（只显示一次，已存 api-key.txt）：%s\n  %s%s%s\n' "$c_dim" "$c_0" "$c_ok" "$API_KEY" "$c_0"
printf '\n  部署信息 ${PREFIX}/部署信息.txt\n\n'
