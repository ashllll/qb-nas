"""
TDD 循环 3: 爬虫并发控制与生命周期管理
验证会话隔离和安全取消（通过 AST 分析，不依赖 Scrapling）
"""

import ast
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from magnet_harvester.config import CrawlerConfig
from magnet_harvester.crawler import MagnetCrawler, ScraplingPageResult


def _get_crawler_source() -> str:
    repo_root = os.path.join(os.path.dirname(__file__), "..")
    with open(
        os.path.join(repo_root, "magnet_harvester", "crawler.py"), "r", encoding="utf-8"
    ) as f:
        return f.read()


def _find_method_source(source: str, method_name: str) -> str:
    """从类源码中提取方法源码（简化版，基于 AST）"""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MagnetCrawler":
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == method_name
                ):
                    start = item.lineno - 1
                    end = item.end_lineno
                    lines = source.splitlines()
                    return "\n".join(lines[start:end])
    return ""


# ═══════════════════════════════════════════════════
# 示踪弹: _global_seen 应为会话级（非实例级）
# ═══════════════════════════════════════════════════


def test_crawler_has_no_instance_level_seen_set():
    """MagnetCrawler 不应在实例级别维护 _global_seen"""
    source = _get_crawler_source()
    # __init__ 中不应创建 _global_seen
    init_source = _find_method_source(source, "__init__")
    assert "_global_seen" not in init_source, "__init__ 中不应有 _global_seen"


# ═══════════════════════════════════════════════════
# 增量测试 2: crawl() 使用 Scrapling 会话
# ═══════════════════════════════════════════════════


def test_crawler_uses_scrapling_session_fetch():
    """_fetch_deep_stream 应使用 Scrapling session.fetch。"""
    source = _get_crawler_source()
    session_source = _find_method_source(source, "_run_crawl_session")
    fetch_deep_source = _find_method_source(source, "_fetch_deep_stream")
    assert "TaskGroup" not in session_source, "不应使用 TaskGroup"
    assert "create_task" not in session_source, "不应在会话层手写 worker task"
    assert "_fetch_page" in fetch_deep_source, "_fetch_deep_stream 应通过 Scrapling 抓页面"
    assert "arun" not in fetch_deep_source, "不应再调用旧爬虫 arun"


# ═══════════════════════════════════════════════════
# 增量测试 3: seen 集合通过参数传递（会话隔离）
# ═══════════════════════════════════════════════════


def test_seen_set_passed_as_parameter():
    """seen 集合应通过参数链传递，而非实例属性"""
    source = _get_crawler_source()

    # crawl() 中应创建局部 seen 集合
    crawl_source = _find_method_source(source, "crawl")
    assert "seen: Set[str] = set()" in crawl_source, "crawl() 中应创建局部 seen"

    # _run_crawl_session 应接收 seen 参数
    session_source = _find_method_source(source, "_run_crawl_session")
    assert "seen: Set[str]" in session_source, "_run_crawl_session 应接收 seen"

    # _handle_crawl_result 应使用 seen 参数（而非 self._global_seen）
    result_source = _find_method_source(source, "_handle_crawl_result")
    assert "self._global_seen" not in result_source, (
        "_handle_crawl_result 不应使用 self._global_seen"
    )
    assert "hash_key in seen" in result_source, "_handle_crawl_result 应使用 seen 参数"


@pytest.mark.asyncio
async def test_http_first_returns_static_page_without_dynamic_fallback():
    class StaticSession:
        async def get(self, _url, **_kwargs):
            return type(
                "Response",
                (),
                {
                    "url": "https://example.com/list",
                    "status": 200,
                    "html_content": '<a href="/torrent/123">详情</a>',
                    "text": "",
                    "body": b"",
                    "encoding": "utf-8",
                },
            )()

    class DynamicSession:
        def __init__(self):
            self.calls = 0

        async def fetch(self, _url, **_kwargs):
            self.calls += 1
            raise AssertionError("静态页面已有详情链接，不应动态回退")

    crawler = MagnetCrawler(config=CrawlerConfig(http_first=True))
    dynamic = DynamicSession()
    crawler._http_session = StaticSession()

    result = await crawler._fetch_page(dynamic, "https://example.com/list")

    assert result.success is True
    assert dynamic.calls == 0


@pytest.mark.asyncio
async def test_http_first_falls_back_for_empty_static_shell():
    class StaticSession:
        async def get(self, _url, **_kwargs):
            return type(
                "Response",
                (),
                {
                    "url": "https://example.com/detail",
                    "status": 200,
                    "html_content": "<html><body><div id='app'></div></body></html>",
                    "text": "",
                    "body": b"",
                    "encoding": "utf-8",
                },
            )()

    class DynamicSession:
        async def fetch(self, _url, **_kwargs):
            return type(
                "Response",
                (),
                {
                    "url": "https://example.com/detail",
                    "status": 200,
                    "html_content": "<html><body>rendered</body></html>",
                    "text": "",
                    "body": b"",
                    "encoding": "utf-8",
                },
            )()

    crawler = MagnetCrawler(config=CrawlerConfig(http_first=True))
    crawler._http_session = StaticSession()

    result = await crawler._fetch_page(DynamicSession(), "https://example.com/detail")

    assert isinstance(result, ScraplingPageResult)
    assert result.html == "<html><body>rendered</body></html>"


@pytest.mark.asyncio
async def test_crawl_session_task_is_owned_by_injected_task_manager():
    class Admission:
        async def admit(self, url):
            return url

        async def admit_redirect_chain(self, url):
            return url

    class Tasks:
        def __init__(self):
            self.names = []

        def create(self, coro, name=None):
            self.names.append(name)
            return asyncio.create_task(coro, name=name)

    tasks = Tasks()
    crawler = MagnetCrawler(
        config=CrawlerConfig(),
        target_admission=Admission(),
        task_manager=tasks,
    )
    crawler._crawler = object()

    async def finish_session(**kwargs):
        await kwargs["events"].put(None)

    crawler._run_crawl_session = finish_session

    messages = [message async for message in crawler.crawl("https://example.com")]

    assert messages == []
    assert tasks.names == ["crawl-session"]


if __name__ == "__main__":
    test_crawler_has_no_instance_level_seen_set()
    print("[PASS] test_crawler_has_no_instance_level_seen_set")

    test_crawler_uses_scrapling_session_fetch()
    print("[PASS] test_crawler_uses_scrapling_session_fetch")

    test_seen_set_passed_as_parameter()
    print("[PASS] test_seen_set_passed_as_parameter")

    print("\n=== TDD Loop 3: Crawler session isolation tests passed! ===")
