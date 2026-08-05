"""FS_BASE_PATH directory creation regression tests."""

from unittest.mock import AsyncMock

import httpx
import pytest

from magnet_harvester.config import QBitConfig
from magnet_harvester.qbit_client import QBittorrentClient
from magnet_harvester.qbit_client.paths import _safe_fs_segment


def test_safe_fs_segment_for_empty_category():
    assert _safe_fs_segment("") == "uncategorized"
    assert _safe_fs_segment("   ") == "uncategorized"
    assert _safe_fs_segment(".") == "uncategorized"


def test_safe_fs_segment_blocks_path_traversal():
    assert _safe_fs_segment("../etc") == "_etc"
    assert _safe_fs_segment("a/b\\c:d") == "a_b_c_d"


def _ok_response() -> httpx.Response:
    return httpx.Response(
        200,
        text="Ok.",
        request=httpx.Request("POST", "http://qbit.test/api/v2/torrents/add"),
    )


@pytest.mark.asyncio
async def test_fs_base_path_empty_does_not_create_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = QBittorrentClient(QBitConfig(host="http://qbit.test", fs_base_path=""))
    client.ensure_category = AsyncMock(return_value=True)
    client._req = AsyncMock(return_value=_ok_response())

    assert await client.add_magnet(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        "电影",
        "/downloads/电影",
    )
    assert not (tmp_path / "电影").exists()


@pytest.mark.asyncio
async def test_configured_fs_base_path_creates_category_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = QBittorrentClient(QBitConfig(host="http://qbit.test", fs_base_path=str(tmp_path)))
    client.ensure_category = AsyncMock(return_value=True)
    client._req = AsyncMock(return_value=_ok_response())

    assert await client.add_magnet(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        "电影",
        "/downloads/电影",
    )
    assert (tmp_path / "电影").is_dir()


@pytest.mark.asyncio
async def test_auto_create_dirs_false_skips_directory_creation(tmp_path, monkeypatch):
    """AUTO_CREATE_DIRS=false 且 FS_BASE_PATH 非空 → 不创建目录。"""
    monkeypatch.chdir(tmp_path)
    client = QBittorrentClient(
        QBitConfig(host="http://qbit.test", fs_base_path=str(tmp_path), auto_create_dirs=False)
    )
    client.ensure_category = AsyncMock(return_value=True)
    client._req = AsyncMock(return_value=_ok_response())

    assert await client.add_magnet(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        "电影",
        "/downloads/电影",
    )
    assert not (tmp_path / "电影").exists()  # 当前实现: 目录被创建


@pytest.mark.asyncio
async def test_auto_create_dirs_default_true_creates_directory(tmp_path, monkeypatch):
    """默认 auto_create_dirs=True 行为与现状一致（创建）。"""
    monkeypatch.chdir(tmp_path)
    client = QBittorrentClient(QBitConfig(host="http://qbit.test", fs_base_path=str(tmp_path)))
    client.ensure_category = AsyncMock(return_value=True)
    client._req = AsyncMock(return_value=_ok_response())

    assert await client.add_magnet(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        "电影",
        "/downloads/电影",
    )
    assert (tmp_path / "电影").is_dir()
