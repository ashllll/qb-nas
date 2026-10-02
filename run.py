#!/usr/bin/env python3
"""Magnet Harvester — 磁力链接采集与分类服务

Usage:
    python run.py                    # 启动服务（地址取 SERVICE_HOST:SERVICE_PORT，默认 127.0.0.1:8899）

不要用 `uvicorn magnet_harvester.main:app --host ...` 直接启动：该路径下应用无法
得知真实绑定地址，启动期鉴权强制会失效。启动入口会设置 MH_BOUND_HOST 把真实
绑定地址告知应用，因此请统一用本脚本。
"""
import os

import uvicorn

from magnet_harvester.config import settings
from magnet_harvester.logger import uvicorn_log_config

if __name__ == "__main__":
    # 告知应用本次真实绑定的地址：启动期鉴权强制必须依据它，而不是 .env 里的
    # SERVICE_HOST —— 否则 --host 覆盖会让非 loopback 暴露被误判为本地绑定。
    os.environ["MH_BOUND_HOST"] = settings.SERVICE_HOST
    uvicorn.run(
        "magnet_harvester.main:app",
        host=settings.SERVICE_HOST,
        port=settings.SERVICE_PORT,
        reload=False,
        log_config=uvicorn_log_config(),
    )
