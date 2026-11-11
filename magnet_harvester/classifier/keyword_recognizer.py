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


def _load_keywords(rules_file: Path = KEYWORD_FILE) -> Optional[List[Dict[str, str]]]:
    """读取关键词配置；配置缺失或解析失败返回 None（区别于合法的空列表）。"""
    try:
        if rules_file.exists():
            with open(rules_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    log.error("分类关键词配置文件格式错误：应为 JSON 对象")
                    return None
                if "keywords" not in data:
                    log.error("分类关键词配置缺少 'keywords' 字段")
                    return None
                keywords = data["keywords"]
                if not isinstance(keywords, list):
                    log.error("分类关键词配置中 'keywords' 应为数组")
                    return None
                valid: List[Dict[str, str]] = []
                for i, entry in enumerate(keywords):
                    if not isinstance(entry, dict):
                        log.warning("分类关键词第 %d 条不是对象，已跳过", i)
                        continue
                    if "keyword" not in entry or "category" not in entry:
                        log.warning(
                            "分类关键词第 %d 条缺少 keyword 或 category 字段，已跳过: %s",
                            i,
                            entry,
                        )
                        continue
                    valid.append(entry)
                return valid
        log.warning(f"分类关键词配置文件不存在: {rules_file}")
        return None
    except Exception as e:
        log.error(f"加载分类关键词配置失败: {e}")
        return None


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
        self._keywords: List[Dict[str, str]] = []
        self._patterns: List[tuple[re.Pattern, Dict[str, str]]] = []
        self._reset()

    def _reset(self) -> bool:
        """重新加载关键词文件；解析成功才整体替换，失败保留旧规则并返回 False。"""
        keywords = _load_keywords(self._rules_file)
        if keywords is None:
            return False
        self._keywords = keywords
        self._patterns = _compile_keyword_patterns(keywords)
        return True

    def recognize(self, name: str) -> Optional[Dict[str, str]]:
        for pattern, rule in self._patterns:
            if pattern.search(name):
                return _rule_result(rule)

        return None

    def reload(self) -> bool:
        return self._reset()

    @classmethod
    def from_keywords(cls, keywords: List[Dict[str, str]]) -> "KeywordCategoryRecognizer":
        """Create a recognizer from inline keyword rules (no JSON file needed)."""
        instance = cls.__new__(cls)
        instance._rules_file = KEYWORD_FILE  # still references default file for reload
        instance._keywords = keywords
        instance._patterns = _compile_keyword_patterns(keywords)
        return instance
