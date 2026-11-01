# NAS 部署（容器化常驻）

把中心服务装成 NAS 上的一个 Docker 容器：开机自己回来、崩了自己重启、数据落在**存储空间 3（/vol3）**、日志能看、只有局域网能连。这一块只加 `deploy/`，不改 app 代码。

- 服务入口：`python -m magnet_harvester.main`（FastAPI + uvicorn，容器内 8899）
- 数据：`/vol3/1000/qb-nas/data/magnet_items.db`（SQLite，持久；容器内挂到 `/data`）
- 镜像层 / 容器：`/vol3/docker`（NAS 的 docker 根本来就在存储空间 3）
- 日志：`docker logs qb-nas-center`（20MB × 5 轮转）
- 凭据：`/vol3/1000/qb-nas/.env`（600）与 `api-key.txt`（600），只在这台 NAS 上

## 文件

| 文件 | 作用 |
| --- | --- |
| `install.sh` | 在 **NAS 上**跑：取代码 → 生成 `.env`/`compose.yaml` → `docker compose build` → 起容器 → 健康检查 |
| `uninstall.sh` | 在 **NAS 上**跑：停容器、删镜像与程序；默认留数据，`--purge --yes` 连数据删 |
| `compose.yaml.tpl` | 生成到 `/vol3/1000/qb-nas/compose.yaml` 的模板（也可直接给 Docker 界面导入） |
| `docker/Dockerfile` | 镜像：`python:3.11-slim` + 依赖 + 代码，非 root(1000:1001) 运行，自带 HEALTHCHECK |
| `docker/dockerignore` | 构建上下文只送 `requirements.txt`、`magnet_harvester/`、`static/` |
| `安装.command` / `卸载.command` / `卸载并删除数据.command` / `状态与日志.command` | Mac 上双击（本质是 ssh 到 NAS 执行上面的脚本） |
| `打包-导入包.sh` | 生成给 NAS Docker 界面手动导入的压缩包（没有 docker 权限时的备用路径） |

## 装（推荐：Mac 上双击）

双击 `安装.command`。它 ssh 到 `llll@192.168.20.193`，用 NAS 仓库里 `stage/resident` 的代码跑 `install.sh`，完成后把地址和 API Key 打出来。

等价的单条命令（在 Mac 上）：

```bash
ssh llll@192.168.20.193 'set -e; tmp=$(mktemp -d); git -C /vol1/1000/github/qb-nas archive stage/resident deploy | tar -x -C "$tmp"; bash "$tmp/deploy/install.sh" --git-ref stage/resident'
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
   - 端口映射 `8899:8899`，重启策略 `unless-stopped`，环境变量文件指向 `.env`。
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

- **只监听局域网**：容器内 `SERVICE_HOST=0.0.0.0`、对外发布 `8899`，NAS 只在局域网里；没有做端口转发。要再收紧可在路由器/fnOS 防火墙上限制来源网段（需要管理员权限，不在这块范围）。
- **IP 是 DHCP**：`192.168.20.193` 由路由器分配（`dynamic`）。Mac 代理填的是这个地址，换 IP 就要改；建议在路由器上给它固定租约。
- **镜像拉取**：NAS 的 `/etc/docker/daemon.json` 已配镜像加速（`docker.fnnas.com` 等）；构建时 PyPI 自动走阿里云镜像，也可 `--pypi-index` 指定。
- **浏览器兜底**：默认只装 HTTP 依赖（`CRAWLER_HTTP_FIRST=true`）；确实需要跑 JS 时加 `--with-browser` 重建镜像（装 Chromium，镜像大 ~400MB）。
- **qBittorrent**：当前 NAS 上没跑、凭据也找不到。缺它只影响 Web UI 的「下载」按钮，入库不受影响；拿到地址/账号后填 `/vol3/1000/qb-nas/.env` 的 `QBIT_*` 再 `docker compose up -d --force-recreate`，或直接在 Web UI 里填。
- **更新代码**：重跑 `install.sh`（幂等，保留 API Key 和数据），会重新构建镜像并替换容器。
- **回滚**：`--git-ref <旧 ref>` 重跑即可；数据是同一份 SQLite，向前兼容。
- **测试/正式隔离**：测试档用独立项目名、容器名、镜像名、端口、目录；收口用 `--test --purge --yes` 清干净。
