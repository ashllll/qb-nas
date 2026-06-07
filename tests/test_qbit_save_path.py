"""
测试 QBittorrentClient.get_default_save_path
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
from magnet_harvester.config import settings
from magnet_harvester.qbit_client import QBittorrentClient


async def test():
    client = QBittorrentClient(config=settings.qbit)
    
    # 先 ping 确认连接
    ok = await client.ping()
    print(f"qB ping: {ok}")
    if not ok:
        print("SKIP: qB not connected")
        return
    
    # 获取默认保存路径
    save_path = await client.get_default_save_path()
    print(f"Default save path: {save_path}")
    assert save_path is not None
    assert len(save_path) > 0
    assert save_path.startswith("/"), f"路径应以 / 开头: {save_path}"
    
    print("=== get_default_save_path test passed! ===")


if __name__ == "__main__":
    asyncio.run(test())
