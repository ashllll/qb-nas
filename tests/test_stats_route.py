"""
Test /api/stats route behavior through the public handler.
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.main import get_stats, stats
from magnet_harvester.store import FakeStore
import magnet_harvester.main as main_module


def test_get_stats_returns_websocket_count_without_global_active_ws():
    store = FakeStore()
    main_module._store = store
    main_module._broadcaster = None

    result = asyncio.run(get_stats())

    assert result["active_items"] == 0
    assert result["websocket_clients"] == 0
    assert "api_calls" in result


if __name__ == "__main__":
    test_get_stats_returns_websocket_count_without_global_active_ws()
    print("=== stats route tests passed! ===")
