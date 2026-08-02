from __future__ import annotations

from pathlib import Path


def test_frontend_handles_canonical_backend_crawl_events():
    """The no-build UI must consume the event names emitted by EventType."""
    source = (Path(__file__).parents[1] / "static" / "index.html").read_text(encoding="utf-8")

    assert 'case "crawl_progress":' in source
    assert 'case "crawl_error":' in source


def test_frontend_does_not_flood_users_with_network_error_toasts():
    source = (Path(__file__).parents[1] / "static" / "index.html").read_text(encoding="utf-8")

    assert "TOAST_DEDUPE_MS" in source
    assert "MAX_VISIBLE_TOASTS" in source
    assert "无法连接 Magnet Harvester 服务" in source


def test_frontend_does_not_mislabel_service_failure_as_qbit_offline():
    source = (Path(__file__).parents[1] / "static" / "index.html").read_text(encoding="utf-8")

    assert 'qbitStatus").textContent = "服务不可达"' in source
