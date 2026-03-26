#!/bin/bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Magnet Harvester — 安装向导"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Python 依赖
echo "[1/3] 安装 Python 依赖..."
pip install -r requirements.txt

# Playwright Chromium
echo "[2/3] 下载 Playwright Chromium..."
playwright install chromium
playwright install-deps chromium 2>/dev/null || true

# 配置文件
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[3/3] 已生成 .env，请编辑填写 MINIMAX_API_KEY 和 QBIT_HOST"
else
    echo "[3/3] .env 已存在，跳过"
fi

echo ""
echo "✅ 安装完成！启动方式："
echo "   cd app && uvicorn main:app --host 0.0.0.0 --port 8899"
echo "   浏览器访问 http://localhost:8899"
