"""
KeywordCategoryRecognizer — generic keyword-based category hints.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
KEYWORD_FILE = CONFIG_DIR / "category_keywords.json"
KEYWORD_BOUNDARY_CHARS = r". _\[\]\(\)\-"


def _load_keywords(rules_file: Path = KEYWORD_FILE) -> List[Dict[str, str]]:
    try:
        if rules_file.exists():
            with open(rules_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    log.error("分类关键词配置文件格式错误：应为 JSON 对象")
                    return []
                return data.get("keywords", [])
        log.warning(f"分类关键词配置文件不存在: {rules_file}")
        return []
    except Exception as e:
        log.error(f"加载分类关键词配置失败: {e}")
        return []


def _compile_keyword_patterns(
    keywords: List[Dict[str, str]],
) -> List[tuple[re.Pattern, Dict[str, str]]]:
    patterns: List[tuple[re.Pattern, Dict[str, str]]] = []
    for rule in keywords:
        keyword = re.escape(rule["keyword"])
        pattern = re.compile(
            rf"(?:^|[{KEYWORD_BOUNDARY_CHARS}])(?:{keyword})(?:$|[{KEYWORD_BOUNDARY_CHARS}])",
            re.IGNORECASE,
        )
        patterns.append((pattern, rule))
    return patterns


def _rule_result(rule: Dict[str, str]) -> Dict[str, str]:
    category = rule["category"]
    return {
        "category": category,
        "save_path": rule.get("save_path", category),
        "keyword": rule["keyword"],
    }


class KeywordCategoryRecognizer:
    """Recognizes category hints from configured filename keywords."""

    def __init__(self, rules_file: Path = KEYWORD_FILE):
        self._rules_file = rules_file
        self._reset()

    def _reset(self):
        """重新加载关键词文件并编译匹配模式（供 __init__ 与 reload 复用）。"""
        self._keywords = _load_keywords(self._rules_file)
        self._patterns = _compile_keyword_patterns(self._keywords)

    def recognize(self, name: str) -> Optional[Dict[str, str]]:
        for pattern, rule in self._patterns:
            if pattern.search(name):
                return _rule_result(rule)

        return None

    def reload(self):
        self._reset()

    @classmethod
    def from_keywords(cls, keywords: List[Dict[str, str]]) -> "KeywordCategoryRecognizer":
        """Create a recognizer from inline keyword rules (no JSON file needed)."""
        instance = cls.__new__(cls)
        instance._rules_file = KEYWORD_FILE  # still references default file for reload
        instance._keywords = keywords
        instance._patterns = _compile_keyword_patterns(keywords)
        return instance
