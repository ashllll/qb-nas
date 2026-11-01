# qb-nas 中心服务 · 由 deploy/install.sh 生成（改配置请改 config/.env 后重跑安装脚本）
#
# 配置的唯一事实源：@@CONFIG_DIR@@/.env（600，属主 llll 1000:1001）
#   · 界面「保存并测试」写它 —— 挂的是整个 config/ 目录，同目录临时文件 + 原子 rename
#   · install.sh 写它 —— 幂等合并：只补缺，不改已有值
#   · 容器启动读它 —— ENV_FILE=/config/.env
#   这里刻意不写 env_file：一旦把 .env 注入进程环境，pydantic 默认的「环境 > 文件」
#   会让启动时的旧值盖回界面刚写入的值（「界面改了没用 / 重启回滚」的根因）。
#
# 三条路径都用这一个文件：
#   · SSH 安装：deploy/install.sh 生成并执行它
#   · NAS Docker 界面「项目/Compose 导入」：把本文件内容粘进去（镜像已导入时把 build 段删掉）
# 所有可写数据只落 /vol3（存储空间 3）：@@CONFIG_DIR@@、@@DATA_DIR@@、@@LOG_DIR@@。
name: @@PROJECT@@

services:
  center:
    build:
      context: @@APP@@
      dockerfile: Dockerfile
      args:
        PYPI_INDEX: "@@PYPI_INDEX@@"
        WITH_BROWSER: "@@WITH_BROWSER@@"
        GIT_COMMIT: "@@GIT_COMMIT@@"
    image: @@IMAGE@@
    container_name: @@CONTAINER@@
    restart: unless-stopped
    init: true
    stop_grace_period: 20s
    # 容器层只读：配置在结构上不可能再落进镜像层（「重建即丢」这条根因被堵死）。
    # 可写面只有三个卷 + /tmp：/config（配置）、/data（SQLite）、/logs（可选日志文件）。
    read_only: true
    tmpfs:
      - /tmp:size=64m,mode=1777
    security_opt:
      - no-new-privileges:true
    environment:
      # 部署键：容器内监听地址/端口/数据库路径，由本文件钉死（.env 不参与）
      SERVICE_HOST: 0.0.0.0
      SERVICE_PORT: "8899"
      STORE_DB_PATH: /data/magnet_items.db
      # 配置文件的指针（不是配置值本身）：容器读它，界面保存也写它
      ENV_FILE: /config/.env
      # 只影响日志与容器内本地钟；数据/接口时间戳一律 UTC-aware（约定 §1）
      TZ: UTC
    # 只监听局域网：绑 NAS 的局域网地址，不占 0.0.0.0 / [::]
    ports:
      - "@@BIND_IP@@:@@HOST_PORT@@:8899"
    volumes:
      - @@CONFIG_DIR@@:/config
      - @@DATA_DIR@@:/data
      - @@LOG_DIR@@:/logs
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "5"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8899/api/health', timeout=4).status == 200 else 1)"]
      interval: 30s
      timeout: 6s
      start_period: 45s
      retries: 3
