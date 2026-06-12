"""
P3-31: 路径丢失前导斜杠测试

缺陷: _extract_base_from_path 返回的路径缺少前导 /（如 "downloads" 而非 "/downloads"）
修复: 返回时添加前导斜杠
"""
import pytest
from magnet_harvester.qbit_client.paths import _extract_base_from_path


def test_extract_base_keeps_leading_slash():
    """验证返回路径有前导斜杠"""
    assert _extract_base_from_path("/vol2/1000/downloads/电影") == "/vol2/1000/downloads"
    assert _extract_base_from_path("/downloads/电视剧") == "/downloads"
    assert _extract_base_from_path("/a/b/c/d") == "/a/b/c"


def test_extract_base_returns_none_for_short_path():
    """单段路径应返回 None"""
    assert _extract_base_from_path("/downloads") is None
    assert _extract_base_from_path("downloads") is None


def test_extract_base_returns_none_for_empty_or_docker_path():
    """空路径或 Docker 路径应返回 None"""
    assert _extract_base_from_path("") is None
    assert _extract_base_from_path("/var/lib") is None
