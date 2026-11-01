#!/usr/bin/env bash
# ============================================================================
# 装完自检（在 NAS 上跑）：容器活着、健康、配置落在 /vol3 的唯一文件上、
# 没有进程环境在抢配置、只监听局域网、崩了会自己回来、Key 生效。
#   bash /vol3/1000/qb-nas/verify.sh                # 正式
#   bash /vol3/1000/qb-nas-test/verify.sh --test    # 测试档
#   --crash  额外做一次 kill -9 自愈测试（会短暂中断服务）
# 全部 PASS 才算立住；任何 FAIL 退出码非 0。
# ============================================================================
set -uo pipefail
PREFIX=${QB_NAS_PREFIX:-/vol3/1000/qb-nas}
CONTAINER=qb-nas-center
HOST_PORT=8899
CRASH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --test) PREFIX=/vol3/1000/qb-nas-test; CONTAINER=qb-nas-center-test; HOST_PORT=18999; shift ;;
    --crash) CRASH=1; shift ;;
    *) echo "不认识的参数：$1" >&2; exit 2 ;;
  esac
done
CONFIG_DIR="$PREFIX/config"
ENV_FILE="$CONFIG_DIR/.env"
KEY=$(grep -E '^API_KEY=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')
PASS=0; FAIL=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
note() { printf '       %s\n' "$*"; }

# 端口只绑局域网地址（见 compose 的 ports），主机侧探活走这个地址
LAN_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)
BASE_URL="http://${LAN_IP:-127.0.0.1}:${HOST_PORT}"

echo "== qb-nas 自检：${PREFIX}（容器 ${CONTAINER}，${BASE_URL}）=="

# 1. docker 权限
if docker info >/dev/null 2>&1; then ok "docker 可用（$(docker info --format '{{.ServerVersion}}')）"; else bad "docker 不可用，后面的检查没有意义"; exit 1; fi

# 2. 容器状态与重启策略
status=$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo missing)
policy=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$CONTAINER" 2>/dev/null || echo '?')
[ "$status" = "running" ] && ok "容器 running" || bad "容器状态=$status"
[ "$policy" = "unless-stopped" ] && ok "重启策略 unless-stopped（崩了/开机都回来）" || bad "重启策略=$policy"

# 3. 健康
hc=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER" 2>/dev/null || echo none)
[ "$hc" = "healthy" ] && ok "docker healthcheck=healthy" || note "healthcheck=${hc}（刚起时 starting 属正常）"
body=$(curl -fsS --max-time 6 "$BASE_URL/api/health" 2>/dev/null || true)
[ -n "$body" ] && ok "HTTP /api/health 200：$body" || bad "HTTP /api/health 无响应"

# 4. Web UI
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "$BASE_URL/" 2>/dev/null || echo 000)
[ "$code" = "200" ] && ok "Web UI / 返回 200" || bad "Web UI / 返回 $code"

# 5. API Key：无 key 应 401，有 key 应 200
if [ -n "$KEY" ]; then
  c1=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$BASE_URL/api/items" 2>/dev/null)
  c2=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -H "X-API-Key: $KEY" "$BASE_URL/api/items" 2>/dev/null)
  [ "$c1" = "401" ] && ok "无 Key 访问被挡（401）" || note "无 Key 访问返回 ${c1}（若该接口不校验可忽略）"
  [ "$c2" = "200" ] && ok "带 Key 访问正常（200）" || bad "带 Key 访问返回 $c2"
else
  bad "读不到 $ENV_FILE 里的 API_KEY"
fi

# 6. 挂载：全在 /vol3，且 /config、/data 都在
mounts=$(docker inspect -f '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' "$CONTAINER" 2>/dev/null)
echo "$mounts" | grep -q '^/vol3/' && ok "挂载都在 /vol3：$(echo "$mounts" | tr '\n' ' ')" || bad "有挂载不在 /vol3：$mounts"
echo "$mounts" | grep -qE "^${CONFIG_DIR} -> /config$" && ok "配置目录已挂载：${CONFIG_DIR} → /config" || bad "缺 /config 挂载（界面写入会落进容器层）"
echo "$mounts" | grep -qE "^${PREFIX}/data -> /data$" && ok "数据目录已挂载：${PREFIX}/data → /data" || bad "缺 /data 挂载"
echo "$mounts" | grep -qE '^/vol[124]/' && bad "发现挂载指向 /vol1|/vol2|/vol4" || true
root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null)
case "$root" in /vol3/*) ok "docker 根在 ${root}（镜像/容器层在存储空间 3）" ;; *) bad "docker 根在 $root" ;; esac
db="$PREFIX/data/magnet_items.db"
if [ -f "$db" ]; then ok "SQLite 存在：${db}（$(du -h "$db" | cut -f1)）"; else note "还没有 ${db}（还没收到第一条数据时属正常）"; fi

# 7. 容器层只读：配置结构上不可能落进镜像层
ro=$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$CONTAINER" 2>/dev/null || echo false)
[ "$ro" = "true" ] && ok "容器根文件系统只读（配置只能写进 /config 卷）" || note "容器根文件系统可写（read_only 未开，仍建议 /config 挂载）"

# 8. 运行身份：容器 uid/gid 必须和 /vol3 上的属主一致
img_user=$(docker inspect -f '{{.Config.User}}' "$CONTAINER" 2>/dev/null || echo '?')
in_id=$(docker exec "$CONTAINER" id 2>/dev/null | head -1 || true)
[ -n "$in_id" ] && ok "容器内身份：${in_id}（镜像 User=${img_user}）" || note "读不到容器内 id（镜像可能没有 id 命令）"

# 9. 配置唯一事实源：文件权限/属主、容器内同一份、可写
if [ -f "$ENV_FILE" ]; then
  mode=$(stat -c %a "$ENV_FILE"); uid=$(stat -c %u "$ENV_FILE"); gid=$(stat -c %g "$ENV_FILE")
  [ "$mode" = "600" ] && ok "配置权限 600" || bad "配置权限 ${mode}（含 API Key/qB 凭据，必须 600）"
  [ "$uid" = "1000" ] && [ "$gid" = "1001" ] && ok "配置属主 ${uid}:${gid}（= 容器运行用户）" || bad "配置属主 ${uid}:${gid}，容器运行 1000:1001（界面保存会 Permission denied）"
  dir_mode=$(stat -c %a "$CONFIG_DIR")
  [ "$dir_mode" = "700" ] && ok "配置目录 700" || note "配置目录 ${dir_mode}（建议 700）"
  inside=$(docker exec "$CONTAINER" printenv ENV_FILE 2>/dev/null || true)
  [ "$inside" = "/config/.env" ] && ok "容器内 ENV_FILE=/config/.env" || bad "容器内 ENV_FILE=${inside:-空}（应为 /config/.env）"
  if docker exec "$CONTAINER" test -w /config 2>/dev/null && docker exec "$CONTAINER" test -w /config/.env 2>/dev/null; then
    ok "容器内对 /config 与 .env 可写（界面保存不会 Permission denied）"
  else
    bad "容器内对 /config(.env) 不可写：界面保存必失败"
  fi
  # 事实源一致性：界面读到的 qB 主机 == 文件里那一行（说明没有进程环境在盖）
  if [ -n "$KEY" ] && command -v python3 >/dev/null; then
    api_host=$(curl -fsS --max-time 6 -H "X-API-Key: $KEY" "$BASE_URL/api/config" 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin).get("qbit_host",""))' 2>/dev/null || true)
    file_host=$(grep -E '^QBIT_HOST=' "$ENV_FILE" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')
    if [ -n "$api_host" ] && [ "$api_host" = "$file_host" ]; then
      ok "运行中的 qB 主机 == 配置文件（界面读的就是这一份）"
    else
      bad "运行中的 qB 主机(${api_host:-?}) ≠ 配置文件(${file_host:-?})：有第二个配置源在盖"
    fi
  fi
else
  bad "没有配置文件 ${ENV_FILE}"
fi

# 10. 没有任何配置值被注入进程环境（旧部署的 env_file 陷阱）
leaked=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$CONTAINER" 2>/dev/null \
  | cut -d= -f1 | grep -E '^(QBIT_|API_KEY$|CRAWLER_|LOG_LEVEL$|STORE_BACKEND$)' | paste -sd' ' -)
[ -z "$leaked" ] && ok "容器进程环境里没有配置键（配置只来自 /config/.env）" || bad "进程环境里仍有配置键：${leaked}（会与文件形成双源）"
tz=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$CONTAINER" 2>/dev/null | grep '^TZ=' || echo 'TZ=(未设)')
note "容器时区：${tz}（只影响日志与本地钟；数据时间戳一律 UTC-aware）"

# 11. 监听：只绑局域网地址，不占 0.0.0.0 / [::]
host_ip="$LAN_IP"
bound=$(docker inspect -f '{{range $p, $b := .NetworkSettings.Ports}}{{range $b}}{{.HostIp}}:{{.HostPort}} {{end}}{{end}}' "$CONTAINER" 2>/dev/null | tr -s ' ' '\n' | grep -E ":${HOST_PORT}$" | head -1)
if [ -n "$bound" ]; then
  bind_ip=${bound%:*}
  if [ "$bind_ip" = "$host_ip" ]; then
    ok "端口只绑局域网地址：${bound}"
  elif [ "$bind_ip" = "0.0.0.0" ] || [ "$bind_ip" = "::" ]; then
    bad "端口绑在所有网卡（${bound}）：应为局域网地址 ${host_ip}"
  else
    note "端口绑定 ${bound}（本机局域网地址 ${host_ip}）"
  fi
else
  bad "没有 ${HOST_PORT} 的端口映射"
fi
listen=$(ss -ltn | awk -v p=":${HOST_PORT}\$" '$4 ~ p {print $4}' | paste -sd' ' -)
[ -n "$listen" ] && ok "监听：$listen" || bad "没在监听 ${HOST_PORT}"

# 12. 日志
n=$(docker logs --tail 5 "$CONTAINER" 2>&1 | wc -l)
[ "$n" -gt 0 ] && ok "docker logs 可看（最近 $n 行）" || bad "docker logs 空的"

# 13. 崩了会自己回来（--crash）
# 必须从容器「内部」杀进程才算真崩溃：docker kill/stop 属于手动停止，
# unless-stopped 的语义是「运维主动停的不自动拉起」（这是对的，不是 bug）。
if [ "$CRASH" = "1" ]; then
  before=$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER")
  t0=$(date +%s)
  # 杀应用进程（不是 PID 1：内核不让从命名空间内部杀死 PID 1，实测 kill -9 1 无效）。
  # 应用进程被 SIGKILL 后 docker-init 退出 → 容器非正常退出 → restart 策略拉起。
  docker exec "$CONTAINER" python -c '
import os, signal
for p in os.listdir("/proc"):
    if not p.isdigit() or p == "1":
        continue
    try:
        cmdline = open("/proc/" + p + "/cmdline", "rb").read()
    except OSError:
        continue
    if b"magnet_harvester.main" in cmdline:
        os.kill(int(p), signal.SIGKILL)
        break
' >/dev/null 2>&1
  back=0
  for i in $(seq 1 60); do
    st=$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null)
    now=$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER" 2>/dev/null)
    if [ "$st" = "running" ] && [ "$now" != "$before" ] && curl -fsS --max-time 2 "$BASE_URL/api/health" >/dev/null 2>&1; then
      back=1; break
    fi
    sleep 1
  done
  t1=$(date +%s)
  [ "$back" = "1" ] && ok "kill -9 后 $((t1-t0))s 自动恢复并健康（前后 StartedAt ${before} → $(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER" 2>/dev/null)）" || bad "kill -9 后 60s 没回来"
  hc=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER")
  note "恢复后 healthcheck=$hc"
  # 崩溃重启后配置仍在（同一份文件、同样的值）
  if [ -f "$ENV_FILE" ]; then
    after_host=$(grep -E '^QBIT_HOST=' "$ENV_FILE" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')
    [ -n "$after_host" ] && ok "崩溃自愈后配置仍在：QBIT_HOST=${after_host}" || bad "崩溃自愈后读不到 QBIT_HOST"
  fi
fi

printf '\n== 结果：%d PASS / %d FAIL ==\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
