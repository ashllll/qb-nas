from __future__ import annotations

from pathlib import Path


def test_frontend_handles_canonical_backend_crawl_events():
    """The no-build UI must consume the event names emitted by EventType."""
    source = (Path(__file__).parents[1] / "static" / "index.html").read_text(encoding="utf-8")

    assert 'case "crawl_progress":' in source
    assert 'case "crawl_error":' in source
