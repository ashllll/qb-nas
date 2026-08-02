"""
ClassificationRule — unified protocol for classifier priority chain.

Every classification rule returns Optional[ClassificationResult] with
a consistent shape: category, confidence, reason, save_path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import re

from magnet_harvester.classifier.fallback import LOCAL_RULES
from magnet_harvester.classifier.keyword_recognizer import KeywordCategoryRecognizer
from magnet_harvester.classifier.studio_recognizer import recognize as studio_recognize


@dataclass
class ClassificationResult:
    """Consistent result shape across all classification rules."""

    category: str
    confidence: str
    reason: str
    save_path: str


@runtime_checkable
class ClassificationRule(Protocol):
    """Protocol for classification rules — returns None if no match."""

    def apply(self, name: str) -> ClassificationResult | None: ...


# ── Adapter wrappers ──────────────────────────


class KeywordRule:
    """Adapter: KeywordCategoryRecognizer → ClassificationRule."""

    def __init__(self, keywords: list[dict] | None = None, rules_file: Path | None = None):
        if keywords is not None:
            self._recognizer = KeywordCategoryRecognizer.from_keywords(keywords)
            self._reloadable = False
        elif rules_file is not None:
            self._recognizer = KeywordCategoryRecognizer(rules_file=rules_file)
            self._reloadable = True
        else:
            self._recognizer = KeywordCategoryRecognizer()
            self._reloadable = True

    def apply(self, name: str) -> ClassificationResult | None:
        result = self._recognizer.recognize(name)
        if result is None:
            return None
        return ClassificationResult(
            category=result["category"],
            confidence="high",
            reason="keyword_rule",
            save_path=result.get("save_path", result["category"]),
        )

    def reload(self) -> bool:
        if not self._reloadable:
            return False
        self._recognizer.reload()
        return True


class StudioRule:
    """Adapter: studio_recognizer → ClassificationRule."""

    def apply(self, name: str) -> ClassificationResult | None:
        result = studio_recognize(name)
        if result is None:
            return None
        return ClassificationResult(
            category=result["category"],
            confidence=result["confidence"],
            reason=result["reason"],
            save_path=result["save_path"],
        )


class FallbackRule:
    """Always returns a result — last rule in the chain.

    Compiles LOCAL_RULES into instance-level compiled patterns so reloads
    or multiple instances don't share mutable module-level state.
    """

    def __init__(self, reason: str = "local_rule"):
        self._reason = reason
        self._compiled_rules: list[tuple[re.Pattern, str]] = [
            (re.compile(pattern, re.IGNORECASE), category) for pattern, category in LOCAL_RULES
        ]

    def reload(self) -> bool:
        self._compiled_rules = [
            (re.compile(pattern, re.IGNORECASE), category) for pattern, category in LOCAL_RULES
        ]
        return True

    def apply(self, name: str) -> ClassificationResult:
        for pattern, category in self._compiled_rules:
            if pattern.search(name):
                return ClassificationResult(
                    category=category,
                    confidence="low",
                    reason=self._reason,
                    save_path=category,
                )
        # No rule matched — fallback to "其他"
        return ClassificationResult(
            category="其他",
            confidence="low",
            reason=self._reason,
            save_path="其他",
        )
