# syntax=docker/dockerfile:1
# qb-nas 中心服务（Magnet Harvester）镜像
#   构建上下文 = 代码根目录；镜像内以 uid 1000 运行，数据和日志走 /vol3 的 bind mount。
FROM python:3.11-slim

# 容器 TZ 只影响日志与容器内本地钟，不参与数据语义：数据/接口时间戳一律
# UTC-aware（约定 §1）。日志用 UTC，便于和宿主机 `date -u` 逐条对账。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    PYTHONPATH=/app \
    TZ=UTC

# 有浏览器兜底时浏览器装在镜像里，不进 /tmp（运行时 /tmp 是 tmpfs）
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

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
# --chown 一次到位：镜像里就是运行用户的文件，不再用 RUN chown -R 复制整个 /app
# （那会多一层、多一份体积，且掩盖「运行时到底以谁的身份写卷」这个问题）。
COPY --chown=1000:1001 magnet_harvester ./magnet_harvester
COPY --chown=1000:1001 static ./static

# 容器内非 root：uid/gid 对齐 NAS 上的 llll(1000:1001)。
# 这一条是配置能落盘的地基：/vol3/.../config 与 /vol3/.../data 都归 1000:1001，
# 容器以同一身份写，不需要任何 chown/chmod 的临时绕过。
USER 1000:1001

EXPOSE 8899

HEALTHCHECK --interval=30s --timeout=6s --start-period=45s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8899/api/health', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "magnet_harvester.main"]
