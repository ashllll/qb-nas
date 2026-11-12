"""站点走查（site_walker）单元测试 — 离线，用假取页器。"""

from __future__ import annotations

import asyncio

import pytest

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.page_fetch import FailureReason, PageFetch
from magnet_harvester.site_walker import (
    MagnetPageWalker,
    extract_detail_links,
    is_detail_url,
)

LIST_HTML = """
<html><body>
  <a href="/torrents/details/101">one</a>
  <a href="/torrents/browse/4/">browse</a>
  <a href="/torrents/details/102">two</a>
  <a href="https://site.test/torrents/details/101#comments">dup</a>
  <a href="/torrents/details/103">three</a>
  <a href="https://other.test/torrents/details/999">other site</a>
</body></html>
"""

DETAIL_HTML = {
    "/torrents/details/101": "<a href='/torrents/details/201'>related</a>",
    "/torrents/details/102": "",
}


class FakeAdmission:
    def __init__(self, blocked: set[str] | None = None) -> None:
        self.blocked = blocked or set()
        self.calls: list[str] = []

    async def admit(self, url: str) -> str:
        self.calls.append(url)
        if url in self.blocked:
            raise ValueError("URL resolves to a private address")
        return url


class FakeFetcher:
    def __init__(
        self,
        pages: dict[str, PageFetch],
        delays: dict[str, float] | None = None,
        browser_pages: dict[str, PageFetch] | None = None,
    ) -> None:
        self.pages = pages
        self.delays = delays or {}
        self.browser_pages = browser_pages or {}
        self.calls: list[str] = []
        self.allow_browser_calls: list[tuple[str, bool]] = []
        self.browser_first_calls: list[tuple[str, bool]] = []

    async def fetch(
        self, url: str, *, allow_browser: bool = True, browser_first: bool = False
    ) -> PageFetch:
        self.calls.append(url)
        self.allow_browser_calls.append((url, allow_browser))
        self.browser_first_calls.append((url, browser_first))
        delay = self.delays.get(url, 0)
        if delay:
            await asyncio.sleep(delay)
        source = self.browser_pages if browser_first and url in self.browser_pages else self.pages
        return source.get(
            url, PageFetch(url=url, status=404, reason=FailureReason.HTTP_ERROR, message="HTTP 404")
        )


def make_walker(fetcher, *, depth=1, max_links=20, root="https://site.test/torrents/search/4/x"):
    return MagnetPageWalker(
        root_url=root,
        depth=depth,
        config=CrawlerConfig(max_detail_links=max_links),
        fetcher=fetcher,
        admission=FakeAdmission(),
    )


def ok(url: str, html: str) -> PageFetch:
    return PageFetch(url=url, status=200, html=html, elapsed_ms=5)


def test_detail_url_recognises_paths_not_listings():
    assert is_detail_url("https://site.test/torrents/details/101")
    assert is_detail_url("https://site.test/torrent/abc")
    assert is_detail_url("https://site.test/view/9")
    assert not is_detail_url("https://site.test/torrents/browse/4/")
    assert not is_detail_url("https://site.test/torrents/search/4/sexart")
    assert not is_detail_url("https://site.test/c/contact/")


def test_extract_detail_links_is_ordered_deduped_same_site():
    links = extract_detail_links(LIST_HTML, "https://site.test/torrents/search/4/x")
    assert links == [
        "https://site.test/torrents/details/101",
        "https://site.test/torrents/details/102",
        "https://site.test/torrents/details/103",
    ]


@pytest.mark.asyncio
async def test_root_is_yielded_first_then_details_as_they_finish():
    fetcher = FakeFetcher(
        {
            "https://site.test/torrents/search/4/x": ok(
                "https://site.test/torrents/search/4/x", LIST_HTML
            ),
            "https://site.test/torrents/details/101": ok(
                "https://site.test/torrents/details/101", "magnet a"
            ),
            "https://site.test/torrents/details/102": ok(
                "https://site.test/torrents/details/102", "magnet b"
            ),
            "https://site.test/torrents/details/103": ok(
                "https://site.test/torrents/details/103", "magnet c"
            ),
        },
        # 102 最快回来 → 它必须排在 101 前面被交出去
        delays={
            "https://site.test/torrents/details/101": 0.05,
            "https://site.test/torrents/details/102": 0.0,
        },
    )
    walker = make_walker(fetcher)
    seen = [event async for event in walker.stream()]
    assert seen[0]["kind"] == "page"
    assert seen[0]["url"].endswith("/torrents/search/4/x")
    assert seen[0]["detail_links"] == 3
    detail_order = [e["url"].rsplit("/", 1)[-1] for e in seen[1:]]
    assert detail_order[0] == "102", f"先回来的详情页应先交付，实际 {detail_order}"
    assert set(detail_order) == {"101", "102", "103"}


@pytest.mark.asyncio
async def test_private_host_root_is_reported_unsafe_url_without_any_fetch():
    root = "https://site.test/torrents/search/4/x"
    fetcher = FakeFetcher({root: ok(root, LIST_HTML)})
    walker = MagnetPageWalker(
        root_url=root,
        depth=1,
        config=CrawlerConfig(max_detail_links=20),
        fetcher=fetcher,
        admission=FakeAdmission(blocked={root}),
    )
    events = [event async for event in walker.stream()]
    assert len(events) == 1
    assert events[0]["reason"] == "unsafe_url"
    assert fetcher.calls == []


@pytest.mark.asyncio
async def test_same_host_links_are_admitted_once():
    """57 个详情链接不该查 57 次 DNS。"""
    root = "https://site.test/torrents/search/4/x"
    admission = FakeAdmission()
    fetcher = FakeFetcher(
        {
            root: ok(root, LIST_HTML),
            "https://site.test/torrents/details/101": ok(
                "https://site.test/torrents/details/101", ""
            ),
            "https://site.test/torrents/details/102": ok(
                "https://site.test/torrents/details/102", ""
            ),
            "https://site.test/torrents/details/103": ok(
                "https://site.test/torrents/details/103", ""
            ),
        }
    )
    walker = MagnetPageWalker(
        root_url=root,
        depth=1,
        config=CrawlerConfig(max_detail_links=20),
        fetcher=fetcher,
        admission=admission,
    )
    [event async for event in walker.stream()]
    assert len(admission.calls) == 1, admission.calls


@pytest.mark.asyncio
async def test_only_one_detail_probe_when_http_never_succeeds():
    """回归：57 个详情页并发时，不能被当成 57 个根页各开一次浏览器。"""
    root = "https://site.test/torrents/search/4/x"
    roots = "".join(f'<a href="/torrents/details/{i}">x</a>' for i in range(101, 106))
    pages = {root: ok(root, roots)}
    for i in range(101, 106):
        url = f"https://site.test/torrents/details/{i}"
        pages[url] = PageFetch(url=url, status=403, reason=FailureReason.BLOCKED)
    fetcher = FakeFetcher(pages)
    [event async for event in make_walker(fetcher).stream()]
    allowed = [u for u, allow in fetcher.allow_browser_calls if allow and u != root]
    assert len(allowed) == 1, allowed


def test_escalation_ladder_rules():
    """浏览器很贵：根页决定；详情页默认纯 HTTP；只有整轮都失败才确认一次。"""
    fresh = make_walker(FakeFetcher({}))
    assert fresh._allow_browser(is_root=True) is True
    assert fresh._allow_browser() is False, "HTTP 能通时，详情页不该默认开浏览器"

    browser_site = make_walker(FakeFetcher({}))
    browser_site._browser_all = True
    assert browser_site._allow_browser() is True


@pytest.mark.asyncio
async def test_blocked_root_opens_browser_for_whole_site():
    root = "https://site.test/torrents/search/4/x"
    fetcher = FakeFetcher(
        {
            root: PageFetch(url=root, status=200, html=LIST_HTML, via="browser"),
            "https://site.test/torrents/details/101": PageFetch(
                url="https://site.test/torrents/details/101", status=200, html="", via="browser"
            ),
        }
    )
    [event async for event in make_walker(fetcher).stream()]
    detail_allows = [allow for url, allow in fetcher.allow_browser_calls if url != root]
    assert detail_allows and all(detail_allows)


@pytest.mark.asyncio
async def test_max_links_caps_requests():
    root = "https://site.test/torrents/search/4/x"
    fetcher = FakeFetcher(
        {
            root: ok(root, LIST_HTML),
            "https://site.test/torrents/details/101": ok(
                "https://site.test/torrents/details/101", ""
            ),
            "https://site.test/torrents/details/102": ok(
                "https://site.test/torrents/details/102", ""
            ),
        }
    )
    walker = make_walker(fetcher, max_links=2)
    events = [event async for event in walker.stream()]
    assert events[0]["detail_links"] == 2
    detail_calls = [c for c in fetcher.calls if c != root]
    assert len(detail_calls) == 2


@pytest.mark.asyncio
async def test_depth_one_does_not_follow_links_found_on_detail_pages():
    root = "https://site.test/torrents/search/4/x"
    fetcher = FakeFetcher(
        {
            root: ok(root, LIST_HTML),
            "https://site.test/torrents/details/101": ok(
                "https://site.test/torrents/details/101", DETAIL_HTML["/torrents/details/101"]
            ),
            "https://site.test/torrents/details/102": ok(
                "https://site.test/torrents/details/102", ""
            ),
            "https://site.test/torrents/details/103": ok(
                "https://site.test/torrents/details/103", ""
            ),
        }
    )
    events = [event async for event in make_walker(fetcher, depth=1).stream()]
    assert all("/201" not in e["url"] for e in events)


@pytest.mark.asyncio
async def test_depth_two_follows_links_found_on_detail_pages():
    root = "https://site.test/torrents/search/4/x"
    fetcher = FakeFetcher(
        {
            root: ok(root, LIST_HTML),
            "https://site.test/torrents/details/101": ok(
                "https://site.test/torrents/details/101", DETAIL_HTML["/torrents/details/101"]
            ),
            "https://site.test/torrents/details/102": ok(
                "https://site.test/torrents/details/102", ""
            ),
            "https://site.test/torrents/details/103": ok(
                "https://site.test/torrents/details/103", ""
            ),
            "https://site.test/torrents/details/201": ok(
                "https://site.test/torrents/details/201", "magnet"
            ),
        }
    )
    events = [event async for event in make_walker(fetcher, depth=2).stream()]
    assert any(e["url"].endswith("/201") for e in events)


@pytest.mark.asyncio
async def test_failed_root_stops_the_walk():
    root = "https://site.test/torrents/search/4/x"
    fetcher = FakeFetcher({root: PageFetch(url=root, status=403, reason=FailureReason.BLOCKED)})
    events = [event async for event in make_walker(fetcher).stream()]
    assert len(events) == 1
    assert events[0]["reason"] == "blocked"
    assert fetcher.calls == [root]


@pytest.mark.asyncio
async def test_detail_root_does_not_expand_related_links():
    """用户复制的就是详情页：别再顺着「相关资源」把一条变成几十条请求。"""
    root = "https://site.test/torrents/details/101"
    related = "".join(f'<a href="/torrents/details/{i}">rel</a>' for i in range(200, 205))
    fetcher = FakeFetcher({root: ok(root, related)})
    events = [event async for event in make_walker(fetcher, root=root).stream()]
    assert len(events) == 1
    assert events[0]["detail_links"] == 0
    assert fetcher.calls == [root]


SPA_SHELL_HTML = """
<html><head><script src="/static/app.js"></script></head>
<body><div id="app"></div><a href="/about">about</a></body></html>
"""


@pytest.mark.asyncio
async def test_spa_shell_is_refetched_in_browser_once_then_walked():
    """根页 0 个详情链接但有脚本：浏览器渲染一次再抽链接，之后详情页不再先付 HTTP。"""
    root = "https://site.test/torrents/search/4/x"
    details = [f"https://site.test/torrents/details/{i}" for i in (101, 102, 103)]
    pages = {root: ok(root, SPA_SHELL_HTML)}
    pages.update({url: ok(url, "") for url in details})
    fetcher = FakeFetcher(
        pages,
        browser_pages={root: PageFetch(url=root, status=200, html=LIST_HTML, via="browser")},
    )

    events = [event async for event in make_walker(fetcher).stream()]

    assert events[0]["url"] == root and events[0]["detail_links"] == 3
    assert len(events) == 4, "根页只交付一次，三个详情页各一次"
    assert fetcher.calls.count(root) == 2, "根页只多取一次，不能每轮重试"
    assert [flag for url, flag in fetcher.browser_first_calls if url == root] == [False, True]
    assert all(fetcher.calls.count(url) == 1 for url in details)
    assert all(flag for url, flag in fetcher.browser_first_calls if url != root)


@pytest.mark.asyncio
async def test_static_page_without_detail_links_is_not_refetched():
    """没有脚本的静态空列表页就是真的空，不该再开浏览器。"""
    root = "https://site.test/torrents/search/4/x"
    fetcher = FakeFetcher({root: ok(root, '<html><body><a href="/about">about</a></body></html>')})

    events = [event async for event in make_walker(fetcher).stream()]

    assert len(events) == 1
    assert events[0]["detail_links"] == 0
    assert fetcher.calls == [root]


@pytest.mark.asyncio
async def test_failed_browser_rescue_does_not_repeat_errors():
    """整轮失败后的浏览器探测也失败时，原始错误已经报过，不能再来一遍。"""
    root = "https://site.test/torrents/search/4/x"
    details = [f"https://site.test/torrents/details/{i}" for i in (101, 102, 103)]
    pages = {root: ok(root, LIST_HTML)}
    pages.update(
        {
            url: PageFetch(url=url, status=404, reason=FailureReason.HTTP_ERROR, message="HTTP 404")
            for url in details
        }
    )

    events = [event async for event in make_walker(FakeFetcher(pages)).stream()]

    assert [event["url"] for event in events] == [root, *details]
    assert all(event["reason"] == "http_error" for event in events[1:])


@pytest.mark.asyncio
async def test_browser_rescue_only_yields_pages_it_actually_saved():
    """探测成功后就地开浏览器，只补发真正救回来的页面。"""
    root = "https://site.test/torrents/search/4/x"
    details = [f"https://site.test/torrents/details/{i}" for i in (101, 102, 103)]
    pages = {root: ok(root, LIST_HTML)}
    pages.update(
        {
            url: PageFetch(url=url, status=404, reason=FailureReason.HTTP_ERROR, message="HTTP 404")
            for url in details
        }
    )
    browser_pages = {
        details[0]: PageFetch(url=details[0], status=200, html="magnet a", via="browser"),
        details[2]: PageFetch(url=details[2], status=200, html="magnet c", via="browser"),
    }
    fetcher = FakeFetcher(pages, browser_pages=browser_pages)

    events = [event async for event in make_walker(fetcher).stream()]

    rescued = [event["url"] for event in events if event["success"]]
    assert rescued == [root, details[0], details[2]]
    assert sum(1 for event in events if event["url"] == details[1]) == 1, (
        "102 浏览器也没救回来，只保留第一轮的错误"
    )
    assert all(fetcher.calls.count(url) == 2 for url in details)
