"""
测试 LocalClassifier + StudioRecognizer 集成
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.classifier.local_classifier import LocalClassifier


def test_adult_studio_overrides_category():
    """成人厂牌命中时，分类名被设为厂牌名"""
    clf = LocalClassifier()
    result = clf.classify_one("SexArt.26.02.01.Bonnie.Dolce.XXX.2160p.MP4-WRB")
    assert result["category"] == "SexArt"
    assert "adult/SexArt" in result["save_path"]


def test_adult_studio_in_batch():
    """批量分类中，成人厂牌条目使用厂牌分类"""
    clf = LocalClassifier()
    items = [
        {"index": 0, "name": "BrazzersExxtra.24.05.16.XXX.1080p"},
        {"index": 1, "name": "Avatar.2022.2160p.BluRay"},
    ]
    results = clf.classify_sync_batch(items)
    assert results[0]["category"] == "Brazzers"
    assert "adult/Brazzers" in results[0]["save_path"]
    assert results[1]["category"] == "电影"


def test_stream_batch_calls_back():
    """stream_batch 模式也触发厂牌识别"""
    clf = LocalClassifier()
    items = [{"index": 0, "name": "Vixen.25.01.01.XXX.2160p"}]
    callbacks = []

    def on_result(idx, r):
        callbacks.append((idx, r))

    import asyncio
    asyncio.run(clf.classify_stream_batch(items, on_result=on_result))

    assert len(callbacks) == 1
    assert callbacks[0][1]["category"] == "Vixen"


if __name__ == "__main__":
    test_adult_studio_overrides_category()
    test_adult_studio_in_batch()
    test_stream_batch_calls_back()
    print("=== Classifier+Studio integration tests passed! ===")
