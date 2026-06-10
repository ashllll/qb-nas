"""
测试 KeywordCategoryRecognizer — 从文件名识别通用分类关键词
"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.keyword_recognizer import KeywordCategoryRecognizer


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
