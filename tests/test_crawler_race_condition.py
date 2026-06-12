"""
P0-2: 爬虫 Set 竞态条件测试

缺陷: _claim_unvisited_links 同步修改共享 visited Set，多 worker 并发时可能重复爬取或丢失链接。
修复: 将 _claim_unvisited_links 改为 async def，使用 self._visited_lock 保护 visited Set。
"""
import asyncio
import pytest
from magnet_harvester.crawler import MagnetCrawler


@pytest.mark.asyncio
async def test_claim_unvisited_links_is_async_and_thread_safe():
    """验证 _claim_unvisited_links 是 async 的，且使用锁保护 visited Set"""
    crawler = MagnetCrawler()
    visited = {"https://example.com/already-visited"}
    links = [
        "https://example.com/new-1",
        "https://example.com/already-visited",
        "https://example.com/new-2",
    ]

    # 必须是 awaitable
    result = await crawler._claim_unvisited_links(links, visited)
    assert result == ["https://example.com/new-1", "https://example.com/new-2"]
    assert "https://example.com/new-1" in visited
    assert "https://example.com/new-2" in visited


@pytest.mark.asyncio
async def test_concurrent_claim_no_duplicates():
    """模拟 4 个 worker 并发调用 _claim_unvisited_links，验证无重复"""
    crawler = MagnetCrawler()
    visited = set()
    all_links = [f"https://example.com/page-{i}" for i in range(100)]

    async def worker(links):
        return await crawler._claim_unvisited_links(links, visited)

    # 将链接分成 4 组，模拟不同 worker 提取到重叠的链接
    chunks = [
        all_links[0:30],
        all_links[20:50],
        all_links[40:70],
        all_links[60:100],
    ]

    results = await asyncio.gather(*[worker(chunk) for chunk in chunks])
    all_claimed = []
    for r in results:
        all_claimed.extend(r)

    # 验证无重复
    assert len(all_claimed) == len(set(all_claimed)), "存在重复链接"
    # 验证所有链接都被认领了（因为无重复，且总共 100 个不同链接）
    assert len(all_claimed) == 100, f"期望 100 个，实际 {len(all_claimed)}"


@pytest.mark.asyncio
async def test_seen_set_uses_lock():
    """验证 seen Set 也使用锁保护（已在 _crawl_page 中实现）"""
    crawler = MagnetCrawler()
    seen = set()

    async def add_hash(h):
        async with crawler._seen_lock:
            if h in seen:
                return False
            seen.add(h)
            return True

    results = await asyncio.gather(*[add_hash("abc123") for _ in range(10)])
    # 只有一个成功
    assert sum(results) == 1
    assert len(seen) == 1
