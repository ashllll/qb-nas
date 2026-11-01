# qb-nas 中心服务 · 由 deploy/install.sh 生成（改配置请改同目录 .env 后重跑安装脚本）
#
# 三条路径都用这一个文件：
#   · SSH 安装：deploy/install.sh 生成并执行它
#   · NAS Docker 界面「项目/Compose 导入」：把本文件内容粘进去（镜像已导入时把 build 段删掉）
# 所有可写数据只落 /vol3（存储空间 3）：@@DATA_DIR@@、@@LOG_DIR@@，镜像层在 /vol3/docker。
name: @@PROJECT@@

services:
  center:
    build:
      context: @@APP@@
      dockerfile: Dockerfile
      args:
        PYPI_INDEX: "@@PYPI_INDEX@@"
        WITH_BROWSER: "@@WITH_BROWSER@@"
    image: @@IMAGE@@
    container_name: @@CONTAINER@@
    restart: unless-stopped
    init: true
    stop_grace_period: 20s
    env_file:
      - @@ENV_FILE@@
    environment:
      # 容器内固定 8899，对外端口由 ports 决定（改端口只改 .env 的 HOST_PORT 或重跑安装脚本）
      SERVICE_HOST: 0.0.0.0
      SERVICE_PORT: "8899"
      STORE_DB_PATH: /data/magnet_items.db
      # 时区：容器默认 UTC，会让界面上的“刚刚上报”显示成 8 小时前
      TZ: Asia/Shanghai
      # qB 配置的持久化目标：落在可写卷上，容器重建不丢
      ENV_FILE: /data/.env
    ports:
      - "@@HOST_PORT@@:8899"
    volumes:
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
