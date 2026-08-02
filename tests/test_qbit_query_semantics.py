"""qB query methods distinguish empty success from transport/protocol failure."""

from __future__ import annotations

import httpx
import pytest

from magnet_harvester.config import QBitConfig
from magnet_harvester.qbit_client import QBittorrentClient


def _response(status: int, *, json_body=None) -> httpx.Response:
    return httpx.Response(
        status,
        json=json_body,
        request=httpx.Request("GET", "http://qbit.test/api/v2/query"),
    )


async def test_categories_returns_empty_dict_for_successful_empty_result():
    client = QBittorrentClient(QBitConfig(host="http://qbit.test"))

    async def request(_method, _path, **_kwargs):
        return _response(200, json_body={})

    client._req = request

    assert await client.get_categories() == {}


async def test_categories_raises_for_protocol_failure():
    client = QBittorrentClient(QBitConfig(host="http://qbit.test"))

    async def request(_method, _path, **_kwargs):
        return _response(503, json_body={})

    client._req = request

    with pytest.raises(RuntimeError, match="categories.*503"):
        await client.get_categories()


async def test_duplicate_lookup_propagates_transport_failure():
    client = QBittorrentClient(QBitConfig(host="http://qbit.test"))

    async def request(_method, _path, **_kwargs):
        raise httpx.ConnectError("offline")

    client._req = request

    with pytest.raises(httpx.ConnectError, match="offline"):
        await client.find_torrent_by_prefix("01234567")
