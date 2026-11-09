"""
测试 KeywordCategoryRecognizer — 从文件名识别通用分类关键词
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.classifier.keyword_recognizer import KeywordCategoryRecognizer


def test_keyword_at_name_start_is_recognized(tmp_path):
    rules_file = tmp_path / "category_keywords.json"
    rules_file.write_text(
        json.dumps({"keywords": [{"keyword": "ubuntu", "category": "软件"}]}),
        encoding="utf-8",
    )

    recognizer = KeywordCategoryRecognizer(rules_file=rules_file)
    result = recognizer.recognize("Ubuntu.24.04.Desktop.iso")

    assert result == {"category": "软件", "save_path": "软件", "keyword": "ubuntu"}


def test_keyword_in_brackets_is_recognized(tmp_path):
    rules_file = tmp_path / "category_keywords.json"
    rules_file.write_text(
        json.dumps({"keywords": [{"keyword": "national geographic", "category": "纪录片"}]}),
        encoding="utf-8",
    )

    recognizer = KeywordCategoryRecognizer(rules_file=rules_file)
    result = recognizer.recognize("Deep Ocean [National Geographic 2024] 2160p")

    assert result is not None
    assert result["category"] == "纪录片"


def test_unmatched_name_returns_none(tmp_path):
    rules_file = tmp_path / "category_keywords.json"
    rules_file.write_text(
        json.dumps({"keywords": [{"keyword": "ubuntu", "category": "软件"}]}),
        encoding="utf-8",
    )

    recognizer = KeywordCategoryRecognizer(rules_file=rules_file)

    assert recognizer.recognize("Avatar.The.Way.of.Water.2022.2160p.BluRay") is None


def test_reload_returns_false_and_keeps_keywords_on_invalid_config(tmp_path):
    """缺失/半截 JSON/顶层非对象/keywords 非数组 → reload False，旧规则不动。"""
    rules_file = tmp_path / "category_keywords.json"
    rules_file.write_text(
        json.dumps({"keywords": [{"keyword": "ubuntu", "category": "软件"}]}),
        encoding="utf-8",
    )
    recognizer = KeywordCategoryRecognizer(rules_file=rules_file)
    expected = {"category": "软件", "save_path": "软件", "keyword": "ubuntu"}

    for bad in ('{"keywords": [', "[]", '{"keywords": "not-a-list"}'):
        rules_file.write_text(bad, encoding="utf-8")
        assert recognizer.reload() is False
        assert recognizer.recognize("Ubuntu.24.04.Desktop.iso") == expected

    rules_file.unlink()
    assert recognizer.reload() is False
    assert recognizer.recognize("Ubuntu.24.04.Desktop.iso") == expected


def test_reload_returns_true_with_empty_keywords(tmp_path):
    """空 keywords 数组合法：reload True，关键词清单变空。"""
    rules_file = tmp_path / "category_keywords.json"
    rules_file.write_text(
        json.dumps({"keywords": [{"keyword": "ubuntu", "category": "软件"}]}),
        encoding="utf-8",
    )
    recognizer = KeywordCategoryRecognizer(rules_file=rules_file)
    assert recognizer.recognize("Ubuntu.24.04.Desktop.iso") is not None

    rules_file.write_text(json.dumps({"keywords": []}), encoding="utf-8")
    assert recognizer.reload() is True
    assert recognizer.recognize("Ubuntu.24.04.Desktop.iso") is None


def test_constructor_with_missing_file_still_works(tmp_path):
    """首次加载即缺失：分类器可构造，只是关键词为空。"""
    recognizer = KeywordCategoryRecognizer(rules_file=tmp_path / "missing.json")
    assert recognizer.recognize("Ubuntu.24.04.Desktop.iso") is None
