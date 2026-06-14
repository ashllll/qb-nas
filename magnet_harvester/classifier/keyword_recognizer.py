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


def _load_keywords(rules_file: Path = KEYWORD_FILE) -> List[Dict[str, str]]:
    try:
        if rules_file.exists():
            with open(rules_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("keywords", [])
        log.warning(f"分类关键词配置文件不存在: {rules_file}")
        return []
    except Exception as e:
        log.error(f"加载分类关键词配置失败: {e}")
        return []


class KeywordCategoryRecognizer:
    """Recognizes category hints from configured filename keywords."""

    def __init__(self, rules_file: Path = KEYWORD_FILE):
        self._rules_file = rules_file
        self._keywords = _load_keywords(rules_file)
        self._patterns: List[tuple[re.Pattern, Dict[str, str]]] = []
        for rule in self._keywords:
            keyword = re.escape(rule["keyword"])
            pattern = re.compile(rf"(?:^|[. _\[\]()\-])(?:{keyword})(?:$|[. _\[\]()\-])", re.IGNORECASE)
            self._patterns.append((pattern, rule))

    def recognize(self, name: str) -> Optional[Dict[str, str]]:
        n = name.lower()
        for rule in self._keywords:
            keyword = rule["keyword"].lower()
            if n.startswith(keyword) or n.startswith(keyword + ".") or n.startswith(keyword + "_"):
                category = rule["category"]
                return {"category": category, "save_path": rule.get("save_path", category), "keyword": rule["keyword"]}

        for pattern, rule in self._patterns:
            if pattern.search(name):
                category = rule["category"]
                return {"category": category, "save_path": rule.get("save_path", category), "keyword": rule["keyword"]}

        return None

    def reload(self):
        self.__init__(self._rules_file)

    @classmethod
    def from_keywords(cls, keywords: List[Dict[str, str]]) -> "KeywordCategoryRecognizer":
        """Create a recognizer from inline keyword rules (no JSON file needed)."""
        instance = cls.__new__(cls)
        instance._rules_file = KEYWORD_FILE  # still references default file for reload
        instance._keywords = keywords
        instance._patterns: List[tuple[re.Pattern, Dict[str, str]]] = []
        for rule in keywords:
            keyword = re.escape(rule["keyword"])
            pattern = re.compile(
                rf"(?:^|[. _\[\]()\-])(?:{keyword})(?:$|[. _\[\]()\-])",
                re.IGNORECASE,
            )
            instance._patterns.append((pattern, rule))
        return instance
