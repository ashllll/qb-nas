"""
测试 qB 客户端本地目录名安全处理
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.qbit_client import _safe_fs_segment


def test_safe_fs_segment_blocks_path_traversal():
    assert _safe_fs_segment("../evil/movie") == "_evil_movie"


def test_safe_fs_segment_falls_back_for_empty_name():
    assert _safe_fs_segment("...") == "uncategorized"


if __name__ == "__main__":
    test_safe_fs_segment_blocks_path_traversal()
    test_safe_fs_segment_falls_back_for_empty_name()
    print("=== qB path safety tests passed! ===")
