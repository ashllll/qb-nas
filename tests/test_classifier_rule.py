"""
Test classifier rule chain — ClassificationRule protocol + unified result type.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.classifier.rule import (
    ClassificationResult,
    ClassificationRule,
    FallbackRule,
    KeywordRule,
    StudioRule,
)


# ── 1. ClassificationResult is a consistent shape ──

def test_classification_result_fields():
    """All rules return the same ClassificationResult shape."""
    r = ClassificationResult(
        category="电影",
        confidence="high",
        reason="test",
        save_path="电影",
    )
    assert r.category == "电影"
    assert r.confidence == "high"
    assert r.reason == "test"
    assert r.save_path == "电影"


# ── 2. Each rule returns None when no match ──

def test_keyword_rule_no_match():
    rule = KeywordRule(keywords=[
        {"keyword": "ubuntu", "category": "软件", "save_path": "软件"},
    ])
    assert rule.apply("Random.Movie.2160p") is None


def test_studio_rule_no_match():
    rule = StudioRule()
    # Empty or too-short strings won't match
    assert rule.apply("") is None
    assert rule.apply("ab") is None  # less than 3 chars


def test_fallback_rule_always_matches():
    rule = FallbackRule()
    r = rule.apply("xyz123")
    assert r is not None
    assert r.category == "其他"
    assert r.confidence == "low"
    assert r.reason == "local_rule"


# ── 3. Each rule returns ClassificationResult on match ──

def test_keyword_rule_match():
    rule = KeywordRule(keywords=[
        {"keyword": "ubuntu", "category": "软件", "save_path": "软件"},
    ])
    r = rule.apply("ubuntu-22.04-live-server-amd64.iso")
    assert r is not None
    assert isinstance(r, ClassificationResult)
    assert r.category == "软件"
    assert r.confidence == "high"
    assert r.reason == "keyword_rule"
    assert r.save_path == "软件"


def test_studio_rule_match():
    rule = StudioRule()
    r = rule.apply("SexArt.24.05.20.Performer.Name.2160p")
    assert r is not None
    assert isinstance(r, ClassificationResult)
    assert r.category == "SexArt"
    assert r.confidence == "high"
    assert r.reason == "studio_rule"


def test_fallback_rule_always_returns():
    rule = FallbackRule()
    r = rule.apply("whatever.random.file")
    assert r is not None
    assert isinstance(r, ClassificationResult)
    assert r.reason == "local_rule"


# ── 4. Protocol compatibility ──

class FakeRule:
    def __init__(self, cat: str | None):
        self._cat = cat

    def apply(self, name: str) -> ClassificationResult | None:
        if self._cat:
            return ClassificationResult(
                category=self._cat, confidence="fake", reason="test", save_path=self._cat,
            )
        return None


def test_protocol_runtime_checkable():
    assert isinstance(FakeRule("电影"), ClassificationRule)


def test_chain_first_match_wins():
    rules: list[ClassificationRule] = [
        FakeRule(None),
        FakeRule("电影"),
        FakeRule("电视剧"),
    ]
    result = None
    for rule in rules:
        result = rule.apply("test")
        if result is not None:
            break
    assert result is not None
    assert result.category == "电影"


if __name__ == "__main__":
    test_classification_result_fields()
    test_keyword_rule_no_match()
    test_studio_rule_no_match()
    test_fallback_rule_always_matches()
    test_keyword_rule_match()
    test_studio_rule_match()
    test_fallback_rule_always_returns()
    test_protocol_runtime_checkable()
    test_chain_first_match_wins()
    print("=== classifier rule tests passed! ===")
