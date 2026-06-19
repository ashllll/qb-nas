"""
Test services/stats.py — SystemStats as a pure dataclass.
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.services.stats import SystemStats


def test_records_counters():
    s = SystemStats()
    s.record_crawl()
    s.record_crawl()
    s.record_download()
    s.record_api_call()

    assert s.crawl_requests == 2
    assert s.download_requests == 1
    assert s.api_calls == 1


def test_as_dict_does_not_access_globals():
    s = SystemStats()
    s.record_crawl()

    result = s.as_dict()

    # Must NOT contain external-dependent fields
    assert "active_items" not in result
    assert "websocket_clients" not in result
    assert "error_stats" not in result

    # Must contain own fields
    assert result["crawl_requests"] == 1
    assert result["download_requests"] == 0
    assert result["api_calls"] == 0
    assert "uptime_sec" in result
    assert "uptime_human" in result


def test_as_dict_uptime_format():
    s = SystemStats()
    s.start_time = time.time() - 3665  # 1h 1m 5s ago

    result = s.as_dict()

    assert result["uptime_human"] == "1h 1m 5s"


if __name__ == "__main__":
    test_records_counters()
    test_as_dict_does_not_access_globals()
    test_as_dict_uptime_format()
    print("=== stats service tests passed! ===")
