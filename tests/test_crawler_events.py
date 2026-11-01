"""MagnetCrawler 事件流契约 — 离线，用假走查器/假取页器。

这些是 p1 依赖的接口性质，改内部实现也不能破：
- found 边找到边吐（不等整轮），done 带齐 metrics；
- 每条失败都有枚举 reason；
- source_url 是用户复制的根 URL，detail_url 是磁力所在页；
- 同一 infohash 的 hex/base32 两种写法只出一条；
- 超时、消费端提前关闭都能收尾，不留悬挂任务。
"""

from __future__ import annotations

import asyncio

import pytest

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.crawler import MagnetCrawler

MAGNET_HEX_A = "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=Alpha.1080p"
MAGNET_HEX_B = "magnet:?xt=urn:btih:" + "b" * 40 + "&dn=Beta.2160p"
# b*40 的 base32 写法（同一个 infohash，另一种编码）
MAGNET_BASE32_B = "magnet:?xt=urn:btih:" + "XO53XO53XO53XO53XO53XO53XO53XO53" + "&dn=Beta.2160p"


class StubAdmission:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.fail_for = fail_for or set()

    async def admit(self, url: str) -> str:
        if url in self.fail_for:
            raise ValueError("URL resolves to a private address")
        return url

    async def close(self) -> None:
        return None


class StubFetcher:
    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def fetch(self, url, *, allow_browser=True):  # pragma: no cover - 走查器被替换
        raise AssertionError("本测试不应真的取页")


def page_event(
    url,
    html="",
    *,
    success=True,
    reason=None,
    message="",
    via="http",
    status=200,
    elapsed_ms=5,
    detail_links=0,
    kind="page",
):
    return {
        "kind": kind,
        "url": url,
        "html": html,
        "success": success,
        "reason": reason,
        "message": message,
        "via": via,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "detail_links": detail_links,
    }


class FakeWalker:
    def __init__(self, events, delay_by_url=None):
        self.events = events
        self.delay_by_url = delay_by_url or {}

    async def stream(self):
        for event in self.events:
            delay = self.delay_by_url.get(event["url"], 0)
            if delay:
                await asyncio.sleep(delay)
            yield event


class ScriptedCrawler(MagnetCrawler):
    def __init__(self, events, *, delay_by_url=None, config=None, **kwargs):
        self._scripted = events
        self._delay_by_url = delay_by_url
        super().__init__(
            config=config or CrawlerConfig(concurrency=2),
            target_admission=StubAdmission(),
            fetcher=StubFetcher(),
            **kwargs,
        )

    def _build_walker(self, root_url, depth):
        return FakeWalker(self._scripted, self._delay_by_url)


async def collect(crawler, url, depth=1):
    return [event async for event in crawler.crawl(url, depth=depth)]


@pytest.mark.asyncio
async def test_found_events_stream_before_done_and_carry_metrics():
    root = "https://site.test/list"
    detail = "https://site.test/details/1"
    crawler = ScriptedCrawler(
        [
            page_event(root, "<a href='/details/1'>x</a>", detail_links=1),
            page_event(detail, f"<a href='{MAGNET_HEX_A}'>{MAGNET_HEX_A}</a>"),
        ]
    )
    events = await collect(crawler, root)
    kinds = [e["type"] for e in events]
    assert kinds.index("found") < kinds.index("done")

    found = [e for e in events if e["type"] == "found"]
    assert len(found) == 1
    item = found[0]["item"]
    assert item["source_url"] == root, "source_url 必须是用户复制的根 URL"
    assert item["detail_url"] == detail, "detail_url 必须指向磁力所在页"
    assert item["hash"] == "A" * 40

    done = events[-1]
    assert done["total"] == 1
    metrics = done["metrics"]
    assert metrics["magnets_found"] == 1
    assert metrics["pages_crawled"] == 2
    assert metrics["browser_used"] is False
    assert isinstance(metrics["first_item_ms"], int)


@pytest.mark.asyncio
async def test_first_item_does_not_wait_for_slowest_page():
    root = "https://site.test/list"
    fast = "https://site.test/details/fast"
    slow = "https://site.test/details/slow"
    crawler = ScriptedCrawler(
        [
            page_event(root, "", detail_links=2),
            page_event(fast, f"<a href='{MAGNET_HEX_A}'>{MAGNET_HEX_A}</a>"),
            page_event(slow, f"<a href='{MAGNET_HEX_B}'>{MAGNET_HEX_B}</a>"),
        ],
        delay_by_url={slow: 0.15},
    )
    events = await collect(crawler, root)
    found = [e for e in events if e["type"] == "found"]
    assert found[0]["item"]["hash"] == "A" * 40
    done = events[-1]
    # first_item_ms 明显小于整轮：首条不等最慢的一页
    assert done["metrics"]["first_item_ms"] < int(done["metrics"]["elapsed_sec"] * 1000)


@pytest.mark.asyncio
async def test_failed_page_reports_reason():
    root = "https://site.test/list"
    crawler = ScriptedCrawler(
        [
            page_event(root, "", detail_links=1),
            page_event(
                "https://site.test/details/9",
                success=False,
                reason="blocked",
                message="HTTP 403 人机校验页",
                status=403,
                detail_links=0,
            ),
        ]
    )
    events = await collect(crawler, root)
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["reason"] == "blocked"
    assert "403" in errors[0]["msg"]
    assert events[-1]["metrics"]["errors_by_reason"] == {"blocked": 1}


@pytest.mark.asyncio
async def test_root_list_page_without_magnets_is_not_an_error():
    root = "https://site.test/list"
    crawler = ScriptedCrawler(
        [
            page_event(root, "<a href='/details/1'>x</a>", detail_links=1),
            page_event(
                "https://site.test/details/1", f"<a href='{MAGNET_HEX_A}'>{MAGNET_HEX_A}</a>"
            ),
        ]
    )
    events = await collect(crawler, root)
    assert [e for e in events if e["type"] == "error"] == []


@pytest.mark.asyncio
async def test_detail_page_without_magnet_reports_no_magnet():
    root = "https://site.test/list"
    crawler = ScriptedCrawler(
        [
            page_event(root, "<a href='/details/1'>x</a>", detail_links=1),
            page_event("https://site.test/details/1", "<html>nothing here</html>"),
        ]
    )
    events = await collect(crawler, root)
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["reason"] == "no_magnet"


@pytest.mark.asyncio
async def test_hex_and_base32_forms_of_same_infohash_are_deduped():
    root = "https://site.test/list"
    crawler = ScriptedCrawler(
        [
            page_event(root, "", detail_links=2),
            page_event("https://site.test/details/1", MAGNET_HEX_B),
            page_event("https://site.test/details/2", MAGNET_BASE32_B),
        ]
    )
    events = await collect(crawler, root)
    found = [e for e in events if e["type"] == "found"]
    assert len(found) == 1, "同一个 infohash 的两种写法必须只出一条"


@pytest.mark.asyncio
async def test_browser_usage_is_reported_in_metrics():
    root = "https://site.test/list"
    crawler = ScriptedCrawler(
        [
            page_event(root, "", detail_links=1, via="browser"),
            page_event("https://site.test/details/1", MAGNET_HEX_A),
        ]
    )
    events = await collect(crawler, root)
    assert events[-1]["metrics"]["browser_used"] is True


@pytest.mark.asyncio
async def test_timeout_yields_error_then_done():
    root = "https://site.test/list"

    class SlowCrawler(ScriptedCrawler):
        def _crawl_timeout(self, depth):
            return 1

    crawler = SlowCrawler([page_event(root, "", detail_links=1)], delay_by_url={root: 0.3})
    # 让这次跑得比 1s 上限更慢：把 walker 里的等待放大
    crawler._delay_by_url = {root: 2.0}
    events = await collect(crawler, root)
    kinds = [e["type"] for e in events]
    assert kinds[-2:] == ["error", "done"] or kinds[-1] == "done"
    assert any(e.get("reason") == "timeout" for e in events if e["type"] == "error")


@pytest.mark.asyncio
async def test_consumer_close_cancels_session_task():
    root = "https://site.test/list"
    crawler = ScriptedCrawler(
        [
            page_event(root, "", detail_links=5),
            page_event("https://site.test/details/1", MAGNET_HEX_A),
        ],
        delay_by_url={root: 0.001},
    )
    stream = crawler.crawl(root)
    first = await stream.__anext__()
    assert first["type"] in {"progress", "found"}
    await stream.aclose()
    assert crawler._session_metrics.get() is None


@pytest.mark.asyncio
async def test_overlapping_sessions_keep_independent_metrics():
    root_a = "https://site.test/a"
    root_b = "https://site.test/b"
    crawler_a = ScriptedCrawler(
        [page_event(root_a, MAGNET_HEX_A), page_event(root_a, MAGNET_HEX_B)]
    )
    crawler_b = ScriptedCrawler([page_event(root_b, MAGNET_HEX_A)])
    first, second = await asyncio.gather(collect(crawler_a, root_a), collect(crawler_b, root_b))
    assert first[-1]["total"] == 2
    assert second[-1]["total"] == 1
    assert first[-1]["metrics"]["magnets_found"] == 2
    assert second[-1]["metrics"]["magnets_found"] == 1


@pytest.mark.asyncio
async def test_result_extraction_does_not_block_event_loop():
    """大页面解析不得阻塞事件循环（to_thread 化）。"""
    magnet = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
    heavy = f"<a href='{magnet}'>{magnet}</a>" * 20000
    root = "https://site.test/big"
    crawler = ScriptedCrawler([page_event(root, heavy, detail_links=1)])
    heartbeat = 0

    async def beat():
        nonlocal heartbeat
        while True:
            await asyncio.sleep(0.002)
            heartbeat += 1

    task = asyncio.create_task(beat())
    try:
        await collect(crawler, root)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert heartbeat >= 15
