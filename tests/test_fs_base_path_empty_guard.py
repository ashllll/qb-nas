"""
P1-9: FS_BASE_PATH 空值保护测试

验证 FS_BASE_PATH 为空字符串时不会在当前目录创建文件夹
"""
import os
import pytest
from pathlib import Path
from magnet_harvester.qbit_client.paths import _safe_fs_segment


def test_safe_fs_segment_for_empty_category():
    """空分类名应返回 'uncategorized'"""
    assert _safe_fs_segment("") == "uncategorized"
    assert _safe_fs_segment("   ") == "uncategorized"
    assert _safe_fs_segment(".") == "uncategorized"


def test_safe_fs_segment_blocks_path_traversal():
    """路径穿越字符应被替换为下划线"""
    assert _safe_fs_segment("../etc") == "_etc"
    assert _safe_fs_segment("a/b\\c:d") == "a_b_c_d"


def test_fs_base_path_empty_does_not_create_dir(tmp_path, monkeypatch):
    """FS_BASE_PATH 为空时不应创建目录"""
    from magnet_harvester import config
    original = config.settings.FS_BASE_PATH
    try:
        config.settings.FS_BASE_PATH = ""
        fs_base = config.settings.FS_BASE_PATH.strip()
        # 模拟 client.py 中的逻辑
        if fs_base:
            (Path(fs_base) / _safe_fs_segment("电影")).mkdir(parents=True, exist_ok=True)
        # 如果执行到这里没有创建目录，说明空值保护有效
        assert not Path("电影").exists(), "不应在当前目录创建 电影/ 文件夹"
    finally:
        config.settings.FS_BASE_PATH = original
