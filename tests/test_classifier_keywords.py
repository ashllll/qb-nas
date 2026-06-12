"""
测试 LocalClassifier + KeywordCategoryRecognizer 集成
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.classifier.local_classifier import LocalClassifier


def test_keyword_rule_overrides_fallback_category():
    """关键词命中时，分类使用关键词配置"""
    clf = LocalClassifier()
    result = clf.classify_one("Ubuntu.24.04.Desktop.iso")
    assert result["category"] == "软件"
    assert result["save_path"] == "软件"
    assert result["reason"] == "keyword_rule"


def test_keyword_rule_in_batch():
    """批量分类中，关键词条目使用配置分类"""
    clf = LocalClassifier()
    items = [
        {"index": 0, "name": "Deep Ocean [National Geographic 2024] 2160p"},
        {"index": 1, "name": "Avatar.2022.2160p.BluRay"},
    ]
    results = clf.classify_sync_batch(items)
    assert results[0]["category"] == "纪录片"
    assert results[0]["save_path"] == "纪录片"
    assert results[1]["category"] == "其他"


def test_stream_batch_calls_back_with_keyword_result():
    """stream_batch 模式也触发关键词识别"""
    clf = LocalClassifier()
    items = [{"index": 0, "name": "Debian.12.Netinst.iso"}]
    callbacks = []

    def on_result(idx, r):
        callbacks.append((idx, r))

    import asyncio
    asyncio.run(clf.classify_stream_batch(items, on_result=on_result))

    assert len(callbacks) == 1
    assert callbacks[0][1]["category"] == "软件"


if __name__ == "__main__":
    test_keyword_rule_overrides_fallback_category()
    test_keyword_rule_in_batch()
    test_stream_batch_calls_back_with_keyword_result()
    print("=== Classifier+Keyword integration tests passed! ===")
