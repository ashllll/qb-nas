"""超长发布名不许把分类器拖成平方级：一个名字背后是整条事件循环。

现场：抓回来的页面标题、磁力的 ``dn`` 都没有长度上限。``StudioRule`` 里的
``_STUDIO_WITH_DATE`` 用惰性嵌套量词 ``([A-Za-z0-9]+(?:[\\s.\\-][A-Za-z0-9]+)*?)``
去凑一个「工作室名 + 日期」，在没有日期的长串上每换一个起点都要把整段扫完，
于是 8KB 名字 ≈ 1.1 秒、64KB ≈ 71 秒——而且分类是同步跑在事件循环里的，
这期间 WebSocket、API、qB 同步全部停摆（无人值守机器上一次就是永久性的「假死」）。
"""

from __future__ import annotations

import time

from magnet_harvester.classifier.local_classifier import LocalClassificationEngine
from magnet_harvester.classifier.studio_recognizer import extract_studio

# 改前没有这个上限（用 1e9 兜底，让改前的红灯落在耗时上而不是 ImportError 上）
try:  # pragma: no cover - 只在改前的代码上报 ImportError
    from magnet_harvester.classifier.limits import MAX_CLASSIFY_CHARS
except ImportError:  # pragma: no cover
    MAX_CLASSIFY_CHARS = 10**9


def _elapsed_ms(func, *args) -> tuple[float, object]:
    start = time.perf_counter()
    result = func(*args)
    return (time.perf_counter() - start) * 1000, result


def test_long_name_classification_stays_cheap():
    """8KB 名字分类必须是毫秒级（改前 1100ms 左右，且随长度平方增长）。"""
    engine = LocalClassificationEngine(cache_enabled=False)
    elapsed_ms, result = _elapsed_ms(engine.classify_name, "x" * 8000)

    assert result["category"] in {"其他", "电影", "电视剧", "动漫", "音乐", "游戏", "软件"}
    assert elapsed_ms < 50, f"8KB 名字分类耗时 {elapsed_ms:.1f}ms，规则里还有平方级回溯"


def test_free_text_long_name_cannot_pin_the_event_loop():
    """64KB 名字（网页标题级别）也不许把 CPU 钉住。"""
    engine = LocalClassificationEngine(cache_enabled=False)
    elapsed_ms, _ = _elapsed_ms(engine.classify_name, "预告片的标题 " * 6000)

    assert elapsed_ms < 100, f"64KB 名字分类耗时 {elapsed_ms:.1f}ms"


def test_studio_recognizer_is_linear_on_long_tokens():
    """工作室识别单独打：无日期的长串必须线性扫过。"""
    elapsed_ms, result = _elapsed_ms(extract_studio, "a" * 8000)

    assert result is None
    assert elapsed_ms < 20, f"extract_studio 在 8KB 名字上耗时 {elapsed_ms:.1f}ms"


def test_long_name_does_not_grow_the_cache_entry():
    """缓存里存的是被截断后的名字，一个 64KB 标题不能把 LRU 撑成几十 MB。"""
    engine = LocalClassificationEngine(cache_enabled=True)
    engine.classify_name("y" * 64000)
    stats = engine.get_cache_stats()["cache"]
    assert stats["size"] == 1
    assert MAX_CLASSIFY_CHARS <= 1024


def test_normal_names_keep_their_category_after_the_cap():
    """截断不能改变正常发布名的分类结果。"""
    engine = LocalClassificationEngine(cache_enabled=False)

    assert engine.classify_name("Some.Show.S01E02.1080p.WEB-DL")["category"] == "电视剧"
    assert engine.classify_name("Example.Movie.2023.2160p.BluRay.x265")["category"] == "电影"
    assert engine.classify_name("Anime.Title.Ep01.1080p.Crunchyroll")["category"] == "动漫"
