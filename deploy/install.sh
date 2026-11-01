#!/usr/bin/env bash
# ============================================================================
# qb-nas 中心服务 · NAS 容器化安装（Docker / docker compose）
#
#   在 NAS 上以 llll 运行（需要能用 docker；见下面「没有 docker 权限怎么办」）。
#   · 代码 → <prefix>/app（构建上下文）
#   · 配置 → <prefix>/config/.env（600）——唯一事实源：界面写它、本脚本写它、容器读它
#   · 数据 → <prefix>/data，日志走 docker logs；两者都在 /vol3（存储空间 3）
#   · 容器 restart=unless-stopped：NAS 重启自己回来，崩了 docker 拉起
#   · 生成 <prefix>/config/.env（600，含随机 API Key）与 <prefix>/compose.yaml
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
GIT_COMMIT_ARG=""
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
ALLOW_FAKE_IP=""
LOG_LEVEL=INFO
BIND_IP=""       # 空=自动探测局域网地址；--bind-ip 可覆盖

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
  --bind-ip ADDR      只在这个局域网地址上监听（默认自动探测，如 192.168.20.193）
  --project NAME      compose 项目名（默认 qb-nas）
  --container NAME    容器名（默认 qb-nas-center）
  --image TAG         镜像标签（默认 qb-nas-center:local）
  --source DIR        用这个目录的代码（默认=本脚本所在仓库）
  --git-ref REF       从 --git-repo 的 git 对象取代码
  --commit SHA        记录本次构建对应的 commit（--source 时用，写进镜像标签）
  --git-repo DIR      NAS 上的代码仓库（默认 /vol1/1000/github/qb-nas）
  --api-key KEY       指定 API Key（默认首次随机生成，之后保留）
  --rotate-key        重新生成 API Key
  --pypi-index URL    构建时用的 PyPI 源（默认自动探测阿里云镜像）
  --qbit-host/-user/-pass   顺带写入 qB 配置（可之后在 Web UI 填）
  --allow-fake-ip 0|1 局域网走 mihomo/Clash fake-IP 时设 1（默认自动探测）
  --with-browser      镜像内装 Chromium（HTTP 优先，通常不需要）
  --test              测试档：prefix=/vol3/1000/qb-nas-test，port=18999，名字都带 -test
  --allow-any-disk    允许装到 /vol3 以外（默认拒绝，用户要求数据只在存储空间 3）
  --no-start          只准备、不构建不启动
  --prepare           只摆文件（代码/.env/compose），不要求 docker
  -h, --help          这份说明
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX=${2:?}; shift 2 ;;
    --port) HOST_PORT=${2:?}; shift 2 ;;
    --bind-ip) BIND_IP=${2:?}; shift 2 ;;
    --project) PROJECT=${2:?}; shift 2 ;;
    --container) CONTAINER=${2:?}; shift 2 ;;
    --image) IMAGE=${2:?}; shift 2 ;;
    --source) SOURCE=${2:?}; shift 2 ;;
    --git-ref) GIT_REF=${2:?}; shift 2 ;;
    --commit) GIT_COMMIT_ARG=${2:?}; shift 2 ;;
    --git-repo) GIT_REPO=${2:?}; shift 2 ;;
    --api-key) API_KEY=${2:?}; shift 2 ;;
    --rotate-key) ROTATE_KEY=1; shift ;;
    --pypi-index) PYPI_INDEX=${2:?}; shift 2 ;;
    --qbit-host) QBIT_HOST=${2:?}; shift 2 ;;
    --qbit-user) QBIT_USER=${2:?}; shift 2 ;;
    --qbit-pass) QBIT_PASS=${2:?}; shift 2 ;;
    --allow-fake-ip) ALLOW_FAKE_IP=${2:?}; shift 2 ;;
    --with-browser) WITH_BROWSER=1; shift ;;
    --allow-any-disk) ALLOW_ANY_DISK=1; shift ;;
    --test) PREFIX=/vol3/1000/qb-nas-test; HOST_PORT=18999; PROJECT=qb-nas-test; \
            CONTAINER=qb-nas-center-test; IMAGE=qb-nas-center:test; MODE_NAME=test; shift ;;
    --no-start) START=0; shift ;;
    --prepare) START=0; shift ;;   # 只摆文件，不构建/不起容器
    -h|--help) usage; exit 0 ;;
    *) die "不认识的参数：$1（--help 看用法）" ;;
  esac
done

APP="$PREFIX/app"
DATA="$PREFIX/data"
LOGS="$PREFIX/logs"
RUN="$PREFIX/run"
CONFIG_DIR="$PREFIX/config"
ENV_FILE="$CONFIG_DIR/.env"
COMPOSE="$PREFIX/compose.yaml"
CONTAINER_UID=1000   # 与 Dockerfile 的 USER 1000:1001 一致
CONTAINER_GID=1001

if [ -z "$BIND_IP" ]; then BIND_IP=$(lan_ip || true); fi
if [ -z "$BIND_IP" ]; then
  BIND_IP=0.0.0.0
  warn "没探测到局域网 IP，退回 0.0.0.0（所有网卡）；可用 --bind-ip 指定"
fi
case "$BIND_IP" in
  *[!0-9.]*) die "非法的 --bind-ip：$BIND_IP（要 IPv4 字面量，如 192.168.20.193）" ;;
esac
# 端口只绑在局域网地址上（verify.sh 会检查），所以主机侧探活也必须走这个地址，
# 不能用 127.0.0.1（那样自检会假失败）。
URL_HOST=$BIND_IP
[ "$URL_HOST" = "0.0.0.0" ] && URL_HOST=127.0.0.1

printf '%s\n' "${c_dim}qb-nas 中心服务 · 容器化安装（${MODE_NAME}）${c_0}"
dim "根目录 ${PREFIX}（数据只在 /vol3）"
dim "配置   ${ENV_FILE}（唯一事实源，600）"
dim "端口   ${BIND_IP}:${HOST_PORT} → 容器 8899（只绑局域网）"
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

DOCKER_OK=1
if ! docker info >/dev/null 2>&1; then
  DOCKER_OK=0
  printf '\n%s! 当前用户 llll 用不了 docker（socket 属 root:docker，660）%s\n' "$c_warn" "$c_0" >&2
  printf '  先把代码/配置/compose 摆到 %s，容器这一步等权限或界面导入。\n' "$PREFIX" >&2
fi
DOCKER_VER=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?')
COMPOSE_VER=$(docker compose version --short 2>/dev/null || echo '?')
info "docker $DOCKER_VER · compose $COMPOSE_VER"
info "docker 根：$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo '?')"
ROOT_DIR=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo "")
case "$ROOT_DIR" in
  /vol3/*) : ;;
  "") warn "读不到 docker 根目录" ;;
  *) warn "docker 根在 ${ROOT_DIR}（不在 /vol3），镜像层可能落在别的存储空间" ;;
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
mkdir -p "$RUN"   # 这一步就要用 run/（临时目录必须落 /vol3）
# 临时目录放在 /vol3 的 run/ 下：这台 NAS（fnOS）的 /tmp 对普通用户不可用
# （mktemp 建得出来，但 rsync 往里写会 Permission denied），run/ 是 700 且同盘，
# 构建上下文也不跨文件系统。
STAGE=$(mktemp -d "$RUN/qb-nas-stage.XXXXXX")
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
  info "来源：$GIT_REPO @ ${GIT_REF}（${COMMIT}）"
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
# 这台 NAS（fnOS/btrfs）新建文件默认 700，从 tar 解出来的甚至会是 000：
# 这样的构建上下文 COPY 进镜像后，容器里的 uid1000 读不了 → 容器崩溃循环
# （实测：PermissionError: '/app/magnet_harvester/__init__.py'）。
# 构建上下文统一归一化成「目录 755 / 文件 644 / 脚本 755」。
find "$STAGE" -type d -exec chmod 755 {} +
find "$STAGE" -type f -exec chmod 644 {} +
find "$STAGE" -type f -name '*.sh' -exec chmod 755 {} +

[ -f "$STAGE/magnet_harvester/main.py" ] || die "代码里没有 magnet_harvester/main.py"
[ -f "$STAGE/requirements.txt" ] || die "代码里没有 requirements.txt"

# --source 默认记 local；要给镜像留下可追溯的 commit 就显式 --commit（或用 --git-ref 自动取）
if [ -n "$GIT_COMMIT_ARG" ]; then
  printf '%s' "$GIT_COMMIT_ARG" | grep -Eq '^[0-9a-f]{7,40}$' \
    || die "--commit 需要 7–40 位十六进制 commit SHA，收到：$GIT_COMMIT_ARG"
  COMMIT=$GIT_COMMIT_ARG
fi
printf '%s\n' "$COMMIT" > "$STAGE/COMMIT"

# ── 2. 落地目录与代码 ────────────────────────────────────────────────────────
step "2/8 落地代码与目录"
# fnOS 上有些目录 stat 出来是 000（实际可写）：先把根目录放开，再往下建
chmod 700 "$PREFIX" 2>/dev/null || true
mkdir -p "$APP" "$CONFIG_DIR" "$DATA" "$LOGS" "$RUN" "$PREFIX/backup" "$PREFIX/venv"
# 给容器 uid1000 一个明确的 700（配置目录必须：界面保存要在里面建临时文件）
chmod 700 "$PREFIX" "$PREFIX/venv" "$CONFIG_DIR" "$PREFIX/data" "$PREFIX/logs" "$PREFIX/run" 2>/dev/null || true
chmod 700 "$DATA" "$RUN"; chmod 750 "$LOGS"
cp -p "$SELF_DIR/docker/Dockerfile" "$APP/Dockerfile"
cp -p "$SELF_DIR/docker/dockerignore" "$APP/.dockerignore"
# 把安装器本身也放到 $PREFIX/deploy（之后重跑不用再从 Mac 传）
rsync -a --delete --exclude '导入包/' "$SELF_DIR/" "$PREFIX/deploy/"
chmod +x "$PREFIX/deploy/"*.sh "$PREFIX/deploy/"*.command 2>/dev/null || true
for helper in uninstall.sh verify.sh; do
  [ -f "$SELF_DIR/$helper" ] && install -m 0755 "$SELF_DIR/$helper" "$PREFIX/$helper"
done
rsync -a --delete --exclude 'Dockerfile' --exclude '.dockerignore' \
  --exclude 'data/' --exclude 'logs/' --exclude '.env' "$STAGE/" "$APP/"
info "构建上下文 ${APP}（${COMMIT}）"

# ── 3. 配置：唯一事实源 <prefix>/config/.env（600）───────────────────────────
step "3/8 写配置（唯一事实源 ${ENV_FILE}，600）"
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR" 2>/dev/null || true

# 旧部署有两处 .env（现实为准，两处都要收进来）：
#   · $PREFIX/.env     —— install.sh 写的，被 compose 的 env_file 注入进程环境
#   · $DATA/.env       —— 上一版 compose 的 ENV_FILE=/data/.env，界面「保存并测试」写这里
# 于是「界面写一份、进程读另一份」，重启就被盖回。迁移时三处按优先级合并：
#   已在 config/.env 的 > 界面写过的 /data/.env > install.sh 写的 $PREFIX/.env
# 最后把两处旧文件归档，避免再出现第二个事实源。
LEGACY_ENV="$PREFIX/.env"
LEGACY_DATA_ENV="$DATA/.env"
OLD_ENV="$CONFIG_DIR/.env.old.$$"
{
  [ -f "$ENV_FILE" ] && cat "$ENV_FILE"
  [ -f "$LEGACY_DATA_ENV" ] && cat "$LEGACY_DATA_ENV"
  [ -f "$LEGACY_ENV" ] && cat "$LEGACY_ENV"
} > "$OLD_ENV" || true
chmod 600 "$OLD_ENV"
[ -s "$OLD_ENV" ] || : > "$OLD_ENV"
if [ -f "$LEGACY_DATA_ENV" ] || [ -f "$LEGACY_ENV" ]; then
  info "迁移旧配置：$([ -f "$LEGACY_DATA_ENV" ] && echo "$LEGACY_DATA_ENV ") $([ -f "$LEGACY_ENV" ] && echo "$LEGACY_ENV ")→ $ENV_FILE（值原样保留）"
fi
[ -f "$ENV_FILE" ] || : > "$ENV_FILE"
chmod 600 "$ENV_FILE"

# 老值一律「原样保留整行」：不解析、不改引号/转义。旧实现用模板整份重写，
# 会把界面上改过的、手改过的键一起冲掉——那正是「一重装就回解放前」。
old_line() { grep -E "^$1=" "$OLD_ENV" 2>/dev/null | head -1 || true; }
# 取第一个「非空」值：迁移时遇到 API_KEY= 这类空行，绝不能因此生成新 Key（不轮换）
old_value() { grep -E "^$1=" "$OLD_ENV" 2>/dev/null | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' | grep -v '^$' | head -1 || true; }
keep_or() { # $1=键 $2=文件里没有时的默认行
  local line
  line=$(old_line "$1")
  if [ -n "$line" ]; then printf '%s\n' "$line"; else printf '%s\n' "$2"; fi
}
managed_or() { # $1=键 $2=显式值（空=沿用旧值）$3=默认行
  if [ -n "$2" ]; then printf '%s=%s\n' "$1" "$2"; else keep_or "$1" "$3"; fi
}

# API Key：显式给了才换；只有 --rotate-key 才重新生成（重装绝不轮换）
OLD_KEY=$(old_value API_KEY)
[ -n "$API_KEY" ] || API_KEY="$OLD_KEY"
if [ -z "$API_KEY" ] || [ "$ROTATE_KEY" = "1" ]; then
  API_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(24))')
  [ "$ROTATE_KEY" = "1" ] && info "已按要求重新生成 API Key"
fi
[ ${#API_KEY} -ge 24 ] || die "API Key 太短（至少 24 字符）"

# fake-IP（mihomo/Clash）：域名解析到 198.18.0.0/15，SSRF 校验必须放行这一段，
# 否则所有公开网址都会被判「不安全」。显式 > 旧值 > 现场探测。
if [ -z "$ALLOW_FAKE_IP" ]; then ALLOW_FAKE_IP=$(old_value CRAWLER_ALLOW_FAKE_IP); fi
if [ -z "$ALLOW_FAKE_IP" ]; then
  if getent hosts example.com 2>/dev/null | grep -qE '^198\.18\.'; then
    ALLOW_FAKE_IP=1
    info "探测到 fake-IP（example.com → 198.18.x.x）：CRAWLER_ALLOW_FAKE_IP=1"
  else
    ALLOW_FAKE_IP=0
  fi
fi

NEW_ENV="$CONFIG_DIR/.env.new.$$"
umask 077
{
  cat <<ENV_HEAD
# qb-nas 中心服务 · 配置唯一事实源（${MODE_NAME}）
#
# 位置    ${ENV_FILE}（600；容器内 /config/.env）
# 谁写    Web UI「保存并测试」；deploy/install.sh（幂等合并：只补缺，不改已有值）
# 谁读    容器启动（ENV_FILE=/config/.env；compose 把 config/ 目录挂到 /config）
# 优先级  本文件 > 进程环境 > 内置默认
#         例外：SERVICE_HOST / SERVICE_PORT / STORE_DB_PATH 由 compose 钉死（部署键）
# 别用 compose 的 env_file / -e 注入这里的键：那会让启动值盖回界面写入（重启回滚）。
# 改完生效：docker compose -f ${COMPOSE} --project-name ${PROJECT} up -d
ENV_HEAD
  printf '\n# 鉴权（Mac 代理用；不轮换）\n'
  printf 'API_KEY=%s\n' "$API_KEY"
  printf '\n# 存储：容器内 /data ← %s（/vol3，持久）\n' "$DATA"
  keep_or STORE_BACKEND 'STORE_BACKEND=sqlite'
  printf '\n# 取页：公开页 HTTP 优先；1080p/720p 也收\n'
  keep_or CRAWLER_HTTP_FIRST 'CRAWLER_HTTP_FIRST=true'
  keep_or CRAWLER_ALLOWED_RESOLUTIONS 'CRAWLER_ALLOWED_RESOLUTIONS='
  printf 'CRAWLER_ALLOW_FAKE_IP=%s\n' "$ALLOW_FAKE_IP"
  keep_or CRAWLER_MAX_DEPTH 'CRAWLER_MAX_DEPTH=2'
  keep_or CRAWLER_TIMEOUT 'CRAWLER_TIMEOUT=30'
  keep_or CRAWLER_CONCURRENCY 'CRAWLER_CONCURRENCY=4'
  printf '\n# 日志：统一 stdout → docker logs（compose 里 20MB×5 轮转）\n'
  keep_or LOG_FILE 'LOG_FILE='
  keep_or LOG_LEVEL "LOG_LEVEL=${LOG_LEVEL}"
  printf '\n# qBittorrent（可空：只影响「下载」按钮，不影响入库）\n'
  managed_or QBIT_HOST "$QBIT_HOST" 'QBIT_HOST='
  managed_or QBIT_USERNAME "$QBIT_USER" 'QBIT_USERNAME='
  managed_or QBIT_PASSWORD "$QBIT_PASS" 'QBIT_PASSWORD='

  # 模板之外的键：原样保留（用户手加的实验开关不该被重装吃掉）
  EXTRA_KEYS=""
  while IFS= read -r key; do
    [ -n "$key" ] || continue
    case " API_KEY STORE_BACKEND CRAWLER_HTTP_FIRST CRAWLER_ALLOWED_RESOLUTIONS CRAWLER_ALLOW_FAKE_IP CRAWLER_MAX_DEPTH CRAWLER_TIMEOUT CRAWLER_CONCURRENCY LOG_FILE LOG_LEVEL QBIT_HOST QBIT_USERNAME QBIT_PASSWORD SERVICE_HOST SERVICE_PORT STORE_DB_PATH ENV_FILE " in
      *" $key "*) continue ;;
    esac
    [ -z "$EXTRA_KEYS" ] && printf '\n# 以下键不在模板里：安装脚本原样保留\n'
    EXTRA_KEYS=1
    old_line "$key"
  done < <(grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' "$OLD_ENV" 2>/dev/null | tr -d '=')
} > "$NEW_ENV"
umask 022
chmod 600 "$NEW_ENV"

if cmp -s "$NEW_ENV" "$ENV_FILE"; then
  info "配置未变：$ENV_FILE"
  rm -f "$NEW_ENV"
else
  cp -p "$ENV_FILE" "$PREFIX/backup/env-$(date +%Y%m%d-%H%M%S).bak" 2>/dev/null || true
  chmod 600 "$PREFIX/backup"/env-*.bak 2>/dev/null || true
  ls -1t "$PREFIX/backup"/env-*.bak 2>/dev/null | tail -n +4 | xargs -r rm -f || true
  # 与界面保存同一套落盘方式：同目录临时文件 + rename（单文件 bind mount 会 EBUSY，
  # 所以挂的是目录）。这一步同时证明这块盘上这套机制可行。
  mv -f "$NEW_ENV" "$ENV_FILE"
  info "配置已更新：$ENV_FILE"
fi
rm -f "$OLD_ENV"

# 落盘自检：用容器运行用户的身份，在这块盘上做一次 tmp + rename
PROBE="$CONFIG_DIR/.write-probe.$$"
if : > "$PROBE" 2>/dev/null && mv -f "$PROBE" "$CONFIG_DIR/.write-probe.done" 2>/dev/null; then
  rm -f "$CONFIG_DIR/.write-probe.done"
  info "落盘自检：config/ 可写（同目录临时文件 + rename；界面保存走同一条路）"
else
  rm -f "$PROBE" "$CONFIG_DIR/.write-probe.done"
  die "config/ 目录不可写，界面保存会失败：$(ls -ldn "$CONFIG_DIR" 2>/dev/null)"
fi

printf '%s\n' "$API_KEY" > "$PREFIX/api-key.txt"; chmod 600 "$PREFIX/api-key.txt"

# 权限/属主：容器以 1000:1001 写这个目录，对不上就是当初那条 Permission denied
CFG_MODE=$(stat -c %a "$ENV_FILE"); CFG_UID=$(stat -c %u "$ENV_FILE"); CFG_GID=$(stat -c %g "$ENV_FILE")
DIR_MODE=$(stat -c %a "$CONFIG_DIR")
[ "$CFG_MODE" = "600" ] || die "配置文件权限 $CFG_MODE，必须是 600（里面有 API Key 与 qB 凭据）"
if [ "$CFG_UID" != "$CONTAINER_UID" ] || [ "$CFG_GID" != "$CONTAINER_GID" ]; then
  warn "配置属主 ${CFG_UID}:${CFG_GID}，容器运行用户 ${CONTAINER_UID}:${CONTAINER_GID}：界面保存会 Permission denied"
  warn "修：sudo chown -R ${CONTAINER_UID}:${CONTAINER_GID} ${CONFIG_DIR}"
fi
info "配置 ${ENV_FILE} · ${CFG_MODE} ${CFG_UID}:${CFG_GID} · 目录 ${DIR_MODE} · Key 存 $PREFIX/api-key.txt"

# 两处旧文件都归档：从这一刻起只有 config/.env 一个事实源
for legacy in "$LEGACY_ENV" "$LEGACY_DATA_ENV"; do
  [ -f "$legacy" ] || continue
  archive="$PREFIX/backup/env-legacy-$(basename "$(dirname "$legacy")")-$(date +%Y%m%d-%H%M%S).bak"
  mv -f "$legacy" "$archive" 2>/dev/null || continue
  chmod 600 "$archive" 2>/dev/null || true
  info "旧配置已归档：$legacy → $archive（不再参与运行）"
done

# ── 4. compose.yaml ──────────────────────────────────────────────────────────
step "4/8 生成 compose.yaml"
# 构建源要「粘住」：--build-arg 变化会让 RUN pip install 整层缓存失效，
# 每次重建都重下 ~200MB（实测在这台 NAS 上多花 10–20 分钟）。
# 优先级：显式参数 > 上次生成的 compose 里那个值 > 环境变量 > 现场探测。
if [ -z "$PYPI_INDEX" ] && [ -f "$COMPOSE" ]; then
  PYPI_INDEX=$(sed -n 's/^ *PYPI_INDEX: "\(.*\)"$/\1/p' "$COMPOSE" 2>/dev/null | head -1)
fi
# 默认用阿里云镜像：这台 NAS 在国内，直连 pypi.org 常超时；要直连就 --pypi-index ""
[ -n "${PYPI_INDEX:-}" ] || PYPI_INDEX=${QB_NAS_PYPI_INDEX:-https://mirrors.aliyun.com/pypi/simple/}
[ -n "$PYPI_INDEX" ] && dim "构建源 $PYPI_INDEX"
sed -e "s|@@PROJECT@@|$PROJECT|g" \
    -e "s|@@APP@@|$APP|g" \
    -e "s|@@IMAGE@@|$IMAGE|g" \
    -e "s|@@CONTAINER@@|$CONTAINER|g" \
    -e "s|@@CONFIG_DIR@@|$CONFIG_DIR|g" \
    -e "s|@@PREFIX@@|$PREFIX|g" \
    -e "s|@@BIND_IP@@|$BIND_IP|g" \
    -e "s|@@HOST_PORT@@|$HOST_PORT|g" \
    -e "s|@@DATA_DIR@@|$DATA|g" \
    -e "s|@@LOG_DIR@@|$LOGS|g" \
    -e "s|@@PYPI_INDEX@@|$PYPI_INDEX|g" \
    -e "s|@@WITH_BROWSER@@|$WITH_BROWSER|g" \
    -e "s|@@GIT_COMMIT@@|$COMMIT|g" \
    "$SELF_DIR/compose.yaml.tpl" > "$COMPOSE"
chmod 640 "$COMPOSE"
# 备用 compose（界面不能构建时用）：官方镜像 + 首次启动装依赖
sed -e "s|@@PREFIX@@|$PREFIX|g" \
    -e "s|@@APP@@|$APP|g" \
    -e "s|@@CONFIG_DIR@@|$CONFIG_DIR|g" \
    -e "s|@@DATA_DIR@@|$DATA|g" \
    -e "s|@@LOG_DIR@@|$LOGS|g" \
    -e "s|@@BIND_IP@@|$BIND_IP|g" \
    -e "s|@@HOST_PORT@@|$HOST_PORT|g" \
    -e "s|@@PROJECT@@|$PROJECT|g" \
    -e "s|@@CONTAINER@@|$CONTAINER|g" \
"$SELF_DIR/docker/compose.nobuild.yaml" > "$PREFIX/compose.nobuild.yaml"
chmod 640 "$PREFIX/compose.nobuild.yaml"
docker compose -f "$COMPOSE" --project-name "$PROJECT" config >/dev/null \
  || die "生成的 compose.yaml 不合法（${COMPOSE}）"
info "$COMPOSE"

# ── 5. 构建 + 启动 ──────────────────────────────────────────────────────────
step "5/8 构建镜像并启动（首次 3–8 分钟，取决于拉基础镜像）"
if [ "$START" = "1" ] && [ "$DOCKER_OK" = "1" ]; then
  BUILD_LOG="$RUN/build.log"
  if docker compose -f "$COMPOSE" --project-name "$PROJECT" build --progress plain >"$BUILD_LOG" 2>&1; then
    tail -n 6 "$BUILD_LOG" | sed 's/^/  /'
  else
    tail -n 40 "$BUILD_LOG" | sed 's/^/  /'
    die "镜像构建失败，完整日志 $BUILD_LOG"
  fi
  docker compose -f "$COMPOSE" --project-name "$PROJECT" up -d --remove-orphans 2>&1 | sed 's/^/  /'
else
  info "跳过构建/启动（$([ "$DOCKER_OK" = "1" ] && echo "--no-start" || echo "没有 docker 权限")）"
fi

# ── 6. 健康检查 ─────────────────────────────────────────────────────────────
step "6/8 健康检查"
HEALTH_URL="http://$URL_HOST:$HOST_PORT/api/health"
if [ "$START" = "1" ] && [ "$DOCKER_OK" = "1" ]; then
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
    die "排查：docker logs ${CONTAINER}；构建日志 $RUN/build.log"
  fi
  info "健康检查通过：$HEALTH_URL"
  HC=$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo unknown)
  info "容器 $CONTAINER · $(docker inspect -f '{{.State.Status}}' "$CONTAINER") · health=$HC"
else
  info "跳过健康检查（构建/启动未执行）"
fi

# ── 7. 结果 ─────────────────────────────────────────────────────────────────
step "7/8 结果"
LAN=$(lan_ip)
if [ "$DOCKER_OK" = "1" ]; then
  IS_LIVE=$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo 'not-running')
  RESTART_POLICY=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$CONTAINER" 2>/dev/null || echo '?')
else
  IS_LIVE='未启动（没有 docker 权限）'; RESTART_POLICY='unless-stopped（配置已写好）'
fi
DB_SIZE=$(du -h "$DATA/magnet_items.db" 2>/dev/null | cut -f1 || true)
cat > "$PREFIX/部署信息.txt" <<INFO_EOF
qb-nas 中心服务 · 部署信息（$MODE_NAME · $(date '+%Y-%m-%d %H:%M:%S')）
------------------------------------------------------------
Web UI    http://$LAN:$HOST_PORT/
健康      http://$LAN:$HOST_PORT/api/health
API Key   见 $PREFIX/api-key.txt（600），Mac 代理第一次配置时粘贴
根目录    ${PREFIX}（存储空间 3 /vol3）
配置      ${ENV_FILE}（600 · 唯一事实源：界面写/脚本写/容器读都是这一份）
代码      ${APP}（${COMMIT}）
数据      $DATA/magnet_items.db（容器内 /data）
镜像      ${IMAGE}（层在 /vol3/docker）
容器      ${CONTAINER}（restart=${RESTART_POLICY}）
日志      docker logs -f ${CONTAINER}（20MB×5 轮转）
compose   $COMPOSE

常用命令（在 NAS 上）：
  docker ps --filter name=$CONTAINER         # 看容器
  docker logs --tail 100 -f $CONTAINER       # 跟日志
  docker compose -f $COMPOSE --project-name $PROJECT restart
  bash $PREFIX/uninstall.sh                  # 卸载（保留数据）
  bash $PREFIX/uninstall.sh --purge --yes    # 卸载并删数据

以后更新代码：重跑 $PREFIX/deploy/install.sh（幂等，保留 Key 和数据）
INFO_EOF
chmod 600 "$PREFIX/部署信息.txt"

printf '\n%s┌────────────────────────────────────────────────────────────┐%s\n' "$c_ok" "$c_0"
if [ "$DOCKER_OK" = "1" ]; then
  printf '%s│%s 装好了：%s\n' "$c_ok" "$c_0" "$MODE_NAME"
else
  printf '%s│%s 文件已摆好（容器未启动）：%s\n' "$c_ok" "$c_0" "$MODE_NAME"
fi
printf '%s│%s 地址    http://%s:%s/\n' "$c_ok" "$c_0" "$LAN" "$HOST_PORT"
printf '%s│%s 健康    %s\n' "$c_ok" "$c_0" "$HEALTH_URL"
printf '%s│%s 容器    %s（status=%s · restart=%s）\n' "$c_ok" "$c_0" "$CONTAINER" "$IS_LIVE" "$RESTART_POLICY"
printf '%s│%s 配置    %s（600，重建/重启都不丢）\n' "$c_ok" "$c_0" "$ENV_FILE"
printf '%s│%s 数据    %s/magnet_items.db%s\n' "$c_ok" "$c_0" "$DATA" "${DB_SIZE:+（${DB_SIZE}）}"
printf '%s│%s 日志    docker logs -f %s\n' "$c_ok" "$c_0" "$CONTAINER"
printf '%s│%s Key     %s/api-key.txt（600）\n' "$c_ok" "$c_0" "$PREFIX"
printf '%s└────────────────────────────────────────────────────────────┘%s\n' "$c_ok" "$c_0"
printf '\n  %s这次生成的 API Key（只显示一次，已存 api-key.txt）：%s\n  %s%s%s\n' "$c_dim" "$c_0" "$c_ok" "$API_KEY" "$c_0"
if [ "$DOCKER_OK" != "1" ]; then
  cat >&2 <<EOF

  文件都摆好了，还差容器这一步。二选一：

  A. 给权限后一条命令（会构建镜像并起容器）：
       sudo usermod -aG docker llll        # 或 fnOS 网页把 llll 勾进 docker 组
       # 重新登录 SSH 后：
       bash $PREFIX/deploy/install.sh

  B. 不动权限，去 fnOS 的 Docker 界面导入：
       · 项目/Compose 新建，粘贴 $PREFIX/compose.yaml（能构建时直接构建）
       · 界面不能构建时，改用 $PREFIX/compose.nobuild.yaml（官方 python 镜像 + 首次启动装依赖）
       · 数据目录 ${DATA}，端口 $HOST_PORT:8899，重启策略 unless-stopped
       · API Key 在 $PREFIX/api-key.txt
EOF
  exit 3
fi

printf '\n  部署信息 ${PREFIX}/部署信息.txt\n\n'
