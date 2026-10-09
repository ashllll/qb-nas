"""回归：两条正则不得在病态输入上退化成 O(n^2)。

背景：`MAGNET_RE` 与 `_STUDIO_WITH_DATE` 的无界量词会在"含大量触发前缀但无
有效匹配"的文本上二次增长。单个被爬页面（内容由站点控制、无需鉴权）即可让
事件循环停摆——`MAGNET_RE` 全程持有 GIL，故 `asyncio.to_thread` 挡不住；
分类则在事件循环内同步执行。

断言用**缩放比**而非绝对耗时：翻倍输入时线性实现约 2×、二次实现约 4×，
阈值取 3.0 以容忍 CI 抖动，同时仍能抓住二次退化。
"""

from __future__ import annotations

import time

import pytest

from magnet_harvester.classifier.studio_recognizer import _STUDIO_WITH_DATE, extract_studio
from magnet_harvester.magnet_parser import MAGNET_RE, extract_from_text

SCALING_LIMIT = 3.0


def _elapsed(func, *args) -> float:
    start = time.perf_counter()
    func(*args)
    return time.perf_counter() - start


def _assert_not_quadratic(run_small, run_large, *, label: str) -> None:
    _elapsed(run_small)  # 预热，避免首次编译/缓存影响
    small = min(_elapsed(run_small) for _ in range(3))
    large = min(_elapsed(run_large) for _ in range(3))
    if small <= 1e-6:
        pytest.skip(f"{label}: 小输入过快，无法测量缩放比")
    ratio = large / small
    assert ratio < SCALING_LIMIT, (
        f"{label} 疑似二次退化：输入翻倍后耗时增至 {ratio:.2f}×（线性应约 2×）。"
        f" small={small:.4f}s large={large:.4f}s"
    )


def test_magnet_re_does_not_scale_quadratically():
    """大量 'magnet:?' 但无 xt 的文本不得使正则二次退化。"""
    small = "magnet:?" * (16 * 1024 // 8)
    large = "magnet:?" * (32 * 1024 // 8)

    assert MAGNET_RE.findall(small) == []
    _assert_not_quadratic(
        lambda: MAGNET_RE.findall(small),
        lambda: MAGNET_RE.findall(large),
        label="MAGNET_RE",
    )


def test_extract_from_text_handles_pathological_input_quickly():
    """端到端：病态页面在合理时间内返回空结果，而不是拖垮整个服务。"""
    payload = "magnet:?" * (256 * 1024 // 8)
    start = time.perf_counter()
    items = extract_from_text(payload)
    elapsed = time.perf_counter() - start

    assert items == []
    assert elapsed < 3.0, f"256KB 病态输入耗时 {elapsed:.2f}s，疑似退化"


def test_studio_regex_does_not_scale_quadratically():
    """长 dn（无日期）不得使厂牌正则二次退化。"""
    small = "a " * 8000
    large = "a " * 16000

    assert _STUDIO_WITH_DATE.search(small) is None
    _assert_not_quadratic(
        lambda: _STUDIO_WITH_DATE.search(small),
        lambda: _STUDIO_WITH_DATE.search(large),
        label="_STUDIO_WITH_DATE",
    )


def test_studio_extraction_still_correct_after_bound():
    """加上限不得改变真实厂牌名的识别结果。"""
    assert extract_studio("Vixen 24 05 20 Example Scene 2160p") is not None
    assert extract_studio("MetArt 23 11 02 Some Model 1080p") is not None
    assert extract_studio("Some Random Release Name Without Date") is None


def test_long_dn_is_classified_quickly():
    """一条带超长 dn 的 magnet 不得让分类阶段长时间占用事件循环。"""
    from magnet_harvester.classifier.local_classifier import LocalClassifier

    name = "a " * 8000
    classifier = LocalClassifier()
    start = time.perf_counter()
    classifier.classify_one(name)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"超长 dn 分类耗时 {elapsed:.2f}s"
