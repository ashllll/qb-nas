#!/usr/bin/env bash
# ============================================================================
# 装完自检（在 NAS 上跑）：容器活着、健康、数据在 /vol3、崩了会自己回来、Key 生效。
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
ENV_FILE="$PREFIX/.env"
KEY=$(grep -E '^API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2-)
PASS=0; FAIL=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
note() { printf '       %s\n' "$*"; }

echo "== qb-nas 自检：${PREFIX}（容器 ${CONTAINER}，端口 ${HOST_PORT}）=="

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
body=$(curl -fsS --max-time 6 "http://127.0.0.1:$HOST_PORT/api/health" 2>/dev/null || true)
[ -n "$body" ] && ok "HTTP /api/health 200：$body" || bad "HTTP /api/health 无响应"

# 4. Web UI
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "http://127.0.0.1:$HOST_PORT/" 2>/dev/null || echo 000)
[ "$code" = "200" ] && ok "Web UI / 返回 200" || bad "Web UI / 返回 $code"

# 5. API Key：无 key 应 401，有 key 应 200
if [ -n "$KEY" ]; then
  c1=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$HOST_PORT/api/items" 2>/dev/null)
  c2=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -H "X-API-Key: $KEY" "http://127.0.0.1:$HOST_PORT/api/items" 2>/dev/null)
  [ "$c1" = "401" ] && ok "无 Key 访问被挡（401）" || note "无 Key 访问返回 ${c1}（若该接口不校验可忽略）"
  [ "$c2" = "200" ] && ok "带 Key 访问正常（200）" || bad "带 Key 访问返回 $c2"
else
  bad "读不到 $ENV_FILE 里的 API_KEY"
fi

# 6. 数据在 /vol3，且没有 bind 到别的存储空间
mounts=$(docker inspect -f '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' "$CONTAINER" 2>/dev/null)
echo "$mounts" | grep -q '^/vol3/' && ok "挂载都在 /vol3：$(echo "$mounts" | tr '\n' ' ')" || bad "有挂载不在 /vol3：$mounts"
root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null)
case "$root" in /vol3/*) ok "docker 根在 ${root}（镜像/容器层在存储空间 3）" ;; *) bad "docker 根在 $root" ;; esac
echo "$mounts" | grep -qE '^/vol[124]/' && bad "发现挂载指向 /vol1|/vol4" || true
db="$PREFIX/data/magnet_items.db"
if [ -f "$db" ]; then ok "SQLite 存在：${db}（$(du -h "$db" | cut -f1)）"; else note "还没有 ${db}（还没收到第一条数据时属正常）"; fi

# 7. 日志
n=$(docker logs --tail 5 "$CONTAINER" 2>&1 | wc -l)
[ "$n" -gt 0 ] && ok "docker logs 可看（最近 $n 行）" || bad "docker logs 空的"

# 8. 监听
listen=$(ss -ltn | awk -v p=":$HOST_PORT" '$4 ~ p {print $4}' | head -3 | paste -sd' ' -)
[ -n "$listen" ] && ok "监听：$listen" || bad "没在监听 $HOST_PORT"

# 9. 崩了会自己回来（--crash）
if [ "$CRASH" = "1" ]; then
  before=$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER")
  t0=$(date +%s)
  docker kill -s KILL "$CONTAINER" >/dev/null 2>&1
  back=0
  for i in $(seq 1 60); do
    st=$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null)
    now=$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER" 2>/dev/null)
    if [ "$st" = "running" ] && [ "$now" != "$before" ] && curl -fsS --max-time 2 "http://127.0.0.1:$HOST_PORT/api/health" >/dev/null 2>&1; then
      back=1; break
    fi
    sleep 1
  done
  t1=$(date +%s)
  [ "$back" = "1" ] && ok "kill -9 后 $((t1-t0))s 自动恢复并健康" || bad "kill -9 后 60s 没回来"
  hc=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER")
  note "恢复后 healthcheck=$hc"
fi

printf '\n== 结果：%d PASS / %d FAIL ==\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
