# syntax=docker/dockerfile:1
# qb-nas 中心服务（Magnet Harvester）镜像
#   构建上下文 = 代码根目录；镜像内以 uid 1000 运行，数据和日志走 /vol3 的 bind mount。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    PYTHONPATH=/app \
    TZ=Asia/Shanghai

# 国内 NAS 直连 pypi.org 常超时：构建时可用 --build-arg PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple/
ARG PYPI_INDEX=""
ARG WITH_BROWSER=0

WORKDIR /app

# 依赖单独一层，改代码不必重装
COPY requirements.txt ./
RUN set -eux; \
    idx=""; \
    if [ -n "$PYPI_INDEX" ]; then idx="--index-url $PYPI_INDEX"; fi; \
    pip install $idx -r requirements.txt

# 需要浏览器兜底时（公开页 HTTP 优先，通常不用）再装 Chromium
RUN set -eux; \
    if [ "$WITH_BROWSER" = "1" ]; then \
      idx=""; [ -n "$PYPI_INDEX" ] && idx="--index-url $PYPI_INDEX"; \
      pip install $idx "playwright>=1.48"; \
      playwright install --with-deps chromium; \
    fi

# 应用代码（static/ 被 FastAPI 直接挂载，必须一起进镜像）
COPY magnet_harvester ./magnet_harvester
COPY static ./static

# /app 归运行用户：qB 配置持久化（persist_qbit_config）默认写包目录旁的 .env，
# 容器里再叠加 ENV_FILE=/data/.env 落到可写卷；这里顺手把 /app 交给运行用户，避免其它写路径再撞权限。
RUN chown -R 1000:1001 /app

# 容器内非 root：uid/gid 对齐 NAS 上的 llll(1000:1001)，/vol3 上落盘的文件用户能直接读写
USER 1000:1001

EXPOSE 8899

HEALTHCHECK --interval=30s --timeout=6s --start-period=45s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8899/api/health', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "magnet_harvester.main"]
