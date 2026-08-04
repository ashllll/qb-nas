"""
Test classifier cache — LocalClassificationEngine caching + stats/clear.

覆盖：命中/未命中计数、hit_rate、clear_cache、reload 失效、结果拷贝隔离、
LocalClassifier 委托接口、cache_enabled=False 旁路。
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.classifier.local_classifier import (
    LocalClassificationEngine,
    LocalClassifier,
)


# ── 1. 命中/未命中统计 ──


def test_first_call_is_miss_second_is_hit():
    engine = LocalClassificationEngine()
    first = engine.classify_name("Some.Movie.2024.2160p")
    second = engine.classify_name("Some.Movie.2024.2160p")
    assert first == second
    stats = engine.get_cache_stats()["cache"]
    assert stats["size"] == 1
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate_percent"] == 50.0


def test_distinct_names_increase_misses():
    engine = LocalClassificationEngine()
    engine.classify_name("Movie.A")
    engine.classify_name("Movie.B")
    stats = engine.get_cache_stats()["cache"]
    assert stats["size"] == 2
    assert stats["misses"] == 2
    assert stats["hits"] == 0
    assert stats["hit_rate_percent"] == 0.0


# ── 2. clear_cache ──


def test_clear_cache_resets_stats_and_storage():
    engine = LocalClassificationEngine()
    engine.classify_name("Movie.A")
    engine.classify_name("Movie.A")
    assert engine.get_cache_stats()["cache"]["size"] == 1
    engine.clear_cache()
    stats = engine.get_cache_stats()["cache"]
    assert stats["size"] == 0
    assert stats["hits"] == 0
    assert stats["misses"] == 0
    assert stats["hit_rate_percent"] == 0.0


# ── 3. reload_rules 使缓存失效 ──


def test_reload_rules_invalidates_cache():
    engine = LocalClassificationEngine()
    engine.classify_name("Movie.A")
    assert engine.get_cache_stats()["cache"]["size"] == 1
    engine.reload_rules()
    assert engine.get_cache_stats()["cache"]["size"] == 0


# ── 4. 缓存结果与调用方隔离（返回拷贝） ──


def test_cache_result_is_copied_to_caller():
    engine = LocalClassificationEngine()
    result = engine.classify_name("Movie.A")
    result["category"] = "被篡改"
    second = engine.classify_name("Movie.A")
    assert second["category"] != "被篡改"


# ── 5. cache_enabled=False 旁路缓存 ──


def test_cache_disabled_never_records():
    engine = LocalClassificationEngine(cache_enabled=False)
    engine.classify_name("Movie.A")
    engine.classify_name("Movie.A")
    stats = engine.get_cache_stats()["cache"]
    assert stats["size"] == 0
    assert stats["misses"] == 0
    assert stats["hits"] == 0


# ── 6. LocalClassifier 委托接口 ──


def test_local_classifier_delegates_cache_api():
    classifier = LocalClassifier()
    classifier.classify_one("Movie.A")
    classifier.classify_one("Movie.A")
    stats = classifier.get_cache_stats()["cache"]
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    classifier.clear_cache()
    assert classifier.get_cache_stats()["cache"]["size"] == 0
