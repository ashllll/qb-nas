"""
测试 LocalClassifier — 纯本地规则分类，无需 AI
"""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.classifier.local_classifier import LocalClassificationEngine, LocalClassifier


def test_local_classification_engine_owns_cache_interface():
    """缓存属于引擎职责：engine 提供真实统计/清理；usage 等协议兼容面留在 LocalClassifier。"""
    engine = LocalClassificationEngine()
    result = engine.classify_name("Avatar.The.Way.of.Water.2022.2160p.BluRay")
    assert result["category"] == "电影"
    assert engine.get_cache_stats()["cache"]["size"] == 1
    engine.clear_cache()
    assert engine.get_cache_stats()["cache"]["size"] == 0
    assert not hasattr(engine, "usage")


def test_tv_series():
    clf = LocalClassifier()
    result = clf.classify_one("Game.of.Thrones.S01E01.1080p")
    assert result["category"] == "电视剧"


def test_anime():
    clf = LocalClassifier()
    result = clf.classify_one("[UHA-WINGS] Kimetsu no Yaiba - 19 [1080p] [Anime]")
    assert result["category"] == "动漫"


def test_music():
    clf = LocalClassifier()
    result = clf.classify_one("Taylor.Swift.Midnights.FLAC.2022")
    assert result["category"] == "音乐"


def test_game():
    clf = LocalClassifier()
    result = clf.classify_one("Zelda.Breath.of.the.Wild.Switch.GOTY")
    assert result["category"] == "游戏"


def test_software():
    clf = LocalClassifier()
    result = clf.classify_one("Adobe.Photoshop.2024.v25.0.Setup.exe")
    assert result["category"] == "软件"


def test_documentary():
    clf = LocalClassifier()
    result = clf.classify_one("BBC.Planet.Earth.III.2023.2160p")
    assert result["category"] == "纪录片"


def test_variety():
    clf = LocalClassifier()
    result = clf.classify_one("奇葩说 第七季 2023 1080p")
    assert result["category"] == "综艺"


def test_movie_default():
    """纯电影名（无剧集/动漫/音乐特征）→ 电影（BluRay 模式匹配）"""
    clf = LocalClassifier()
    result = clf.classify_one("Avatar.The.Way.of.Water.2022.2160p.BluRay")
    assert result["category"] == "电影"


def test_resolution_without_release_source_is_not_movie_default():
    """普通标题+年份+分辨率不自动归电影，避免过宽 fallback。"""
    clf = LocalClassifier()
    result = clf.classify_one("Avatar.2022.2160p")
    assert result["category"] == "其他"


def test_unknown_default():
    """完全无法识别的名称 → 其他"""
    clf = LocalClassifier()
    result = clf.classify_one("abc123xyz")
    assert result["category"] == "其他"


def test_batch_classify():
    clf = LocalClassifier()
    items = [
        {"index": 0, "name": "Game.of.Thrones.S01E01"},
        {"index": 1, "name": "Taylor.Swift.Midnights.FLAC"},
        {"index": 2, "name": "Avatar.2022.2160p"},
    ]
    results = clf.classify_sync_batch(items)
    assert len(results) == 3
    assert results[0]["category"] == "电视剧"
    assert results[1]["category"] == "音乐"
    assert results[2]["category"] == "其他"


def test_reload_rules_uses_updated_keyword_file(tmp_path):
    rules_file = tmp_path / "category_keywords.json"
    rules_file.write_text(
        json.dumps(
            {
                "keywords": [
                    {"keyword": "OldKey", "category": "电影", "save_path": "电影"},
                ]
            }
        ),
        encoding="utf-8",
    )
    clf = LocalClassifier(keyword_rules_file=rules_file)

    assert clf.classify_one("OldKey.Release")["category"] == "电影"
    assert clf.classify_one("NewKey.Release")["category"] == "其他"

    rules_file.write_text(
        json.dumps(
            {
                "keywords": [
                    {"keyword": "NewKey", "category": "游戏", "save_path": "游戏"},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert clf.reload_rules() == {"status": "reloaded", "rules_reloaded": 2}
    assert clf.classify_one("OldKey.Release")["category"] == "其他"
    assert clf.classify_one("NewKey.Release")["category"] == "游戏"


if __name__ == "__main__":
    test_tv_series()
    test_anime()
    test_music()
    test_game()
    test_software()
    test_documentary()
    test_variety()
    test_movie_default()
    test_unknown_default()
    test_batch_classify()
    print("=== LocalClassifier tests passed! ===")
