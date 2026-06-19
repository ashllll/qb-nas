"""
P3-23: 默认分类过于武断测试

缺陷: classify_local 在未匹配任何规则时默认返回 "电影"，很多内容会被错误分类
修复: 默认返回 "其他"
"""

from magnet_harvester.classifier.fallback import classify_local, make_fallback


def test_classify_local_defaults_to_other():
    """未匹配任何规则时应返回 '其他'"""
    assert classify_local("random_unknown_file") == "其他"
    assert classify_local("Some Generic Document") == "其他"
    assert classify_local("abcdefg") == "其他"


def test_classify_local_still_matches_rules():
    """匹配规则时仍应正确分类"""
    assert classify_local("My Anime Show") == "动漫"
    assert classify_local("Season 1 Episode 2") == "电视剧"
    assert classify_local("Documentary BBC") == "纪录片"


def test_make_fallback_uses_other_as_default():
    """make_fallback 的默认分类应为 '其他'"""
    result = make_fallback("unknown_file")
    assert result["category"] == "其他"
    assert result["confidence"] == "low"
