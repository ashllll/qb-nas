"""FS_BASE_PATH directory creation regression tests."""
from unittest.mock import AsyncMock

import httpx
import pytest

from magnet_harvester.config import QBitConfig, settings
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
    monkeypatch.setattr(settings, "FS_BASE_PATH", "")
    client = QBittorrentClient(QBitConfig(host="http://qbit.test"))
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
    monkeypatch.setattr(settings, "FS_BASE_PATH", str(tmp_path))
    client = QBittorrentClient(QBitConfig(host="http://qbit.test"))
    client.ensure_category = AsyncMock(return_value=True)
    client._req = AsyncMock(return_value=_ok_response())

    assert await client.add_magnet(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        "电影",
        "/downloads/电影",
    )
    assert (tmp_path / "电影").is_dir()
