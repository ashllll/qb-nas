# NAS 部署（容器化常驻）

把中心服务装成 NAS 上的一个 Docker 容器：开机自己回来、崩了自己重启、数据落在**存储空间 3（/vol3）**、日志能看、只有局域网能连。这一块只加 `deploy/`，不改 app 代码。

- 服务入口：`python -m magnet_harvester.main`（FastAPI + uvicorn，容器内 8899）
- 数据：`/vol3/1000/qb-nas/data/magnet_items.db`（SQLite，持久；容器内挂到 `/data`）
- 镜像层 / 容器：`/vol3/docker`（NAS 的 docker 根本来就在存储空间 3）
- 日志：`docker logs qb-nas-center`（20MB × 5 轮转）
- 配置：`/vol3/1000/qb-nas/config/.env`（600）——**唯一事实源**：界面写它、install.sh 写它、容器读它
- 凭据：上面那个 `.env` 与 `api-key.txt`（600），只在这台 NAS 上

## 文件

| 文件 | 作用 |
| --- | --- |
| `install.sh` | 在 **NAS 上**跑：取代码 → 幂等合并 `config/.env` → 生成 `compose.yaml` → `docker compose build` → 起容器 → 健康检查 |
| `uninstall.sh` | 在 **NAS 上**跑：停容器、删镜像与程序；默认留数据，`--purge --yes` 连数据删 |
| `compose.yaml.tpl` | 生成到 `/vol3/1000/qb-nas/compose.yaml` 的模板（也可直接给 Docker 界面导入） |
| `verify.sh` | 装完自检：容器/健康/Key/挂载/配置事实源/无环境注入/只监听局域网/kill -9 自愈 |
| `docker/Dockerfile` | 镜像：`python:3.11-slim` + 依赖 + 代码，非 root(1000:1001) 运行，自带 HEALTHCHECK |
| `docker/dockerignore` | 构建上下文只送 `requirements.txt`、`magnet_harvester/`、`static/` |
| `安装.command` / `卸载.command` / `卸载并删除数据.command` / `状态与日志.command` | Mac 上双击（本质是 ssh 到 NAS 执行上面的脚本） |
| `打包-导入包.sh` | 生成给 NAS Docker 界面手动导入的压缩包（没有 docker 权限时的备用路径） |

## 配置的唯一事实源（别绕开它）

只有一份配置文件：`/vol3/1000/qb-nas/config/.env`（600，属主 llll `1000:1001`）。

| 谁 | 怎么碰它 |
| --- | --- |
| Web UI「保存并测试」 | 经 `/config` 目录挂载写它：同目录临时文件 + 原子 rename（单文件 bind mount 会 EBUSY，所以挂的是目录） |
| `install.sh` | 幂等合并：只补缺、只改部署派生键；界面改过的值原样保留（整行保留，不重排引号/转义） |
| 容器启动 | `ENV_FILE=/config/.env` 读它；compose **不用** `env_file`，进程环境里没有 `QBIT_*`/`API_KEY` |

优先级（`magnet_harvester/config.py` 里是唯一实现，测试锁住）：
**文件 > 进程环境 > 内置默认**；例外是 `SERVICE_HOST` / `SERVICE_PORT` / `STORE_DB_PATH`
这三个部署键，由 compose 钉死（防止 `.env` 与端口映射对不上）。

为什么不能用 `env_file`：pydantic 默认「环境 > 文件」，一旦把 `.env` 注入进程环境，
启动时的旧值会盖回界面刚写入的值——表现就是「界面改了没用、一重启回滚」。

容器根文件系统是**只读**的（compose `read_only: true`），可写面只有 `/config`、`/data`、
`/logs` 和 tmpfs 的 `/tmp`：配置在结构上不可能再落进镜像层，重建也不会丢。

启动日志第一行会报事实源（路径/是否可写/uid/gid/每个键来自 file 还是 env），
出问题时先看它：`docker logs qb-nas-center | head -5`。

## 时区裁定

容器 `TZ=UTC` 只影响日志与容器内本地钟；**数据与接口时间戳一律 UTC-aware**（约定 §1），
与容器 TZ 无关。日志用 UTC，便于和宿主机 `date -u` 逐条对账。要本地时间看日志：
`docker logs -t ... | ...` 或在你自己机器上换算；不要靠改容器 TZ 来「修」界面时间。

## 装（推荐：Mac 上双击）

双击 `安装.command`。它 ssh 到 `llll@192.168.20.193`，用 NAS 仓库里 `stage/resident` 的代码跑 `install.sh`，完成后把地址和 API Key 打出来。

等价的单条命令（在 Mac 上）：

```bash
ssh llll@192.168.20.193 'set -e; tmp=$(mktemp -d); git -C /vol1/1000/github/qb-nas archive stage/resident deploy | tar -x -C "$tmp"; bash "$tmp/deploy/install.sh" --git-ref stage/resident'
```

只摆文件、不要求 docker 权限（会生成 `config/.env`/API Key/compose，容器等权限或界面导入）：

```bash
bash deploy/install.sh --prepare          # 正式目录 /vol3/1000/qb-nas
bash deploy/install.sh --test --prepare   # 测试目录
```

测试档（不碰正式数据/端口）：

```bash
bash deploy/install.sh --test      # /vol3/1000/qb-nas-test + 18999 + qb-nas-test 项目
```

收口：`bash deploy/uninstall.sh --test --purge --yes` 清测试，再按上面装正式。

## 没有 docker 权限怎么办

现场：`docker` CLI 28.5.2、compose v2.40.3 都在，dockerd 常驻，socket `/var/run/docker.sock` 是 `root:docker 660`，`llll` 不在 `docker` 组、`sudo` 要密码。三条路径：

1. **把 `llll` 加进 docker 组（推荐，一次动作，之后全自动）**
   - NAS 终端：`sudo usermod -aG docker llll`，然后断开重连 SSH（新会话才带新组）；
   - 或 fnOS 网页 → 用户与群组 → `llll` → 编辑 → 用户组勾选 `docker` → 保存 → 重连 SSH。
2. **用 sudo 跑一次**：`sudo -v` 拿到票据后立刻 `sudo bash deploy/install.sh`。不推荐：`/vol3/1000/qb-nas` 会变成 root 所有，之后普通用户改配置/看库都别扭。
3. **交给 NAS 的 Docker 界面导入**：运行 `bash deploy/打包-导入包.sh`（在能跑 docker 的机器上会顺手 `docker save` 出镜像 tar；在 NAS 上没权限时只打代码+compose），然后在 fnOS Docker 界面：
   - 「镜像」→ 导入 `qb-nas-center-*.tar`（如果有）；
   - 「项目 / Compose」→ 新建，粘 `compose.yaml`（删掉 `build:` 段，`image:` 指向导入的镜像），数据目录选 `/vol3/1000/qb-nas/data`；
   - 端口映射 `8899:8899`（或只绑局域网 `192.168.20.193:8899:8899`），重启策略 `unless-stopped`；
     配置目录 `config/` 挂到 `/config`，`ENV_FILE=/config/.env`，**不要**加 `env_file`。
   界面不支持 compose 构建时，用导入的镜像跑，不依赖 `build`。

## 装完怎么确认

```bash
docker ps --filter name=qb-nas-center             # Up (healthy)，restart=unless-stopped
curl http://127.0.0.1:8899/api/health             # {"ok":true,...}
docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' qb-nas-center   # unless-stopped
docker kill -s KILL qb-nas-center && sleep 5 && docker ps --filter name=qb-nas-center  # 自己回来
docker restart qb-nas-center && curl ...           # 数据还在（同一份 SQLite）
ss -ltn | grep 8899                                # 监听面
docker logs --tail 50 qb-nas-center
```

## 坑

- **只监听局域网**：默认把端口绑在 NAS 的局域网地址上（`--bind-ip`，自动探测，如 `192.168.20.193:8899`），不占 `0.0.0.0`/`[::]`；`verify.sh` 会检查绑定地址。换 IP 后重跑 install.sh 即可。
- **IP 是 DHCP**：`192.168.20.193` 由路由器分配（`dynamic`）。Mac 代理填的是这个地址，换 IP 就要改；建议在路由器上给它固定租约。
- **镜像拉取**：NAS 的 `/etc/docker/daemon.json` 已配镜像加速（`docker.fnnas.com` 等）；构建时 PyPI 自动走阿里云镜像，也可 `--pypi-index` 指定。
- **浏览器兜底**：默认只装 HTTP 依赖（`CRAWLER_HTTP_FIRST=true`）；确实需要跑 JS 时加 `--with-browser` 重建镜像（装 Chromium，镜像大 ~400MB）。
- **qBittorrent（已装好）**：NAS 上是 Docker 容器，WebUI `http://192.168.20.193:8085`，版本 5.2.4.10（WebAPI 2.15.1）。凭据只写在 `config/.env` 的 `QBIT_*`（600）。v5 与 v4 的差异已在代码里兼容：登录 204 + `QBT_SID_<端口>` cookie；`torrents/add` 返回 JSON。入库阶段完全不碰 qB，只有页面点「下载」才提交，提交后立即开始下载。
- **fake-IP 必须放行**：这台局域网（Mac 与 NAS 都一样）DNS 把公开域名解析到 `198.18.x.x`（mihomo/Clash fake-IP）。`config/.env` 里 `CRAWLER_ALLOW_FAKE_IP=1` 不能删，否则所有网址都会被 SSRF 校验判成「不安全」。`install.sh` 会自动探测并写入。
- **docker logs 里的 Key 已脱敏**：WebSocket 握手把 Key 放查询参数，uvicorn 会写日志；`logger.py` 已加 `RedactingDefaultFormatter`，日志里只会看到 `api_key=***`。
- **自检**：装完跑 `bash /vol3/1000/qb-nas/verify.sh --crash`：容器/health/API Key/挂载只在 /vol3/
  配置 600 与属主/容器内 `/config` 可写/进程环境没有配置键/监听只绑局域网/SQLite/崩溃自愈。
  自愈用「容器内杀掉应用进程」模拟真崩溃；`docker kill/stop` 是手动停止，`unless-stopped`
  故意不自动拉起（那是运维主动停，不是故障）。
- **更新代码**：重跑 `install.sh`（幂等：保留 API Key、界面改过的配置、数据），会重新构建镜像并替换容器。
- **重建/重启不丢配置**：配置在 `/vol3/.../config/.env`，容器层只读；`docker compose up -d --build --force-recreate`
  之后值不变。旧部署的 `/vol3/1000/qb-nas/.env` 会在第一次跑新版 install.sh 时自动迁到 `config/.env` 并归档旧文件。
- **回滚**：`--git-ref <旧 ref>` 重跑即可；数据是同一份 SQLite，向前兼容。
- **测试/正式隔离**：测试档用独立项目名、容器名、镜像名、端口、目录；收口用 `--test --purge --yes` 清干净。
