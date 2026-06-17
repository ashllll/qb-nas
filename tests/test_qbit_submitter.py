"""Focused tests for the internal magnet submitter."""
from unittest.mock import AsyncMock

import httpx
import pytest

from magnet_harvester.qbit_client.submitter import MagnetSubmitter


def _ok_response() -> httpx.Response:
    return httpx.Response(
        200,
        text="Ok.",
        request=httpx.Request("POST", "http://qbit.test/api/v2/torrents/add"),
    )


def _fail_response(text: str = "Fails.") -> httpx.Response:
    return httpx.Response(
        200,
        text=text,
        request=httpx.Request("POST", "http://qbit.test/api/v2/torrents/add"),
    )


def _make_submitter(**overrides):
    return MagnetSubmitter(
        request=overrides.get("request", AsyncMock(return_value=_ok_response())),
        ensure_category=overrides.get("ensure_category", AsyncMock(return_value=True)),
        get_base_save_path=overrides.get("get_base_save_path", AsyncMock(return_value="/downloads")),
        find_torrent_by_prefix=overrides.get("find_torrent_by_prefix", AsyncMock(return_value=None)),
        fs_base_path=overrides.get("fs_base_path", ""),
        set_last_error=overrides.get("set_last_error"),
        record_attempt=overrides.get("record_attempt"),
        record_success=overrides.get("record_success"),
        record_failure=overrides.get("record_failure"),
    )


@pytest.mark.asyncio
async def test_submitter_succeeds_on_ok_response():
    request = AsyncMock(return_value=_ok_response())
    successes = []

    submitter = _make_submitter(
        request=request,
        record_success=lambda: successes.append(True),
    )

    assert await submitter.add_magnet(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        "电影",
        "/downloads/电影",
    )

    request.assert_awaited_once()
    _, kwargs = request.await_args
    assert kwargs["data"]["urls"] == "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
    assert kwargs["data"]["category"] == "电影"
    assert kwargs["data"]["use_auto_torrent_management"] == "true"
    assert "savepath" not in kwargs["data"]
    assert len(successes) == 1


@pytest.mark.asyncio
async def test_submitter_fails_on_invalid_magnet():
    failures = []
    errors = []

    submitter = _make_submitter(
        record_failure=lambda: failures.append(True),
        set_last_error=errors.append,
    )

    assert not await submitter.add_magnet("not-a-magnet", "电影", "")
    assert len(failures) == 1
    assert errors and "btih" in errors[-1]


@pytest.mark.asyncio
async def test_submitter_treats_duplicate_as_success():
    request = AsyncMock(return_value=_fail_response())
    find = AsyncMock(return_value={"hash": "01234567", "name": "Existing Movie"})
    successes = []

    submitter = _make_submitter(
        request=request,
        find_torrent_by_prefix=find,
        record_success=lambda: successes.append(True),
    )

    assert await submitter.add_magnet(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        "电影",
        "/downloads/电影",
    )

    find.assert_awaited_once_with("01234567")
    assert len(successes) == 1


@pytest.mark.asyncio
async def test_submitter_fails_when_qb_rejects_and_no_duplicate():
    request = AsyncMock(return_value=_fail_response("Unknown error"))
    failures = []
    errors = []

    submitter = _make_submitter(
        request=request,
        record_failure=lambda: failures.append(True),
        set_last_error=errors.append,
    )

    assert not await submitter.add_magnet(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        "电影",
        "/downloads/电影",
    )

    assert len(failures) == 1
    assert errors and "qB 拒绝" in errors[-1]


@pytest.mark.asyncio
async def test_submitter_creates_fs_directory_when_configured(tmp_path):
    request = AsyncMock(return_value=_ok_response())

    submitter = _make_submitter(
        request=request,
        fs_base_path=str(tmp_path),
    )

    assert await submitter.add_magnet(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        "电影",
        "/downloads/电影",
    )

    assert (tmp_path / "电影").is_dir()


@pytest.mark.asyncio
async def test_submitter_resolves_relative_save_path():
    request = AsyncMock(return_value=_ok_response())
    ensure = AsyncMock(return_value=True)

    submitter = _make_submitter(
        request=request,
        ensure_category=ensure,
        get_base_save_path=AsyncMock(return_value="/volume1/downloads"),
    )

    assert await submitter.add_magnet(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        "电影",
        "电影",
    )

    ensure.assert_awaited_once_with("电影", "/volume1/downloads/电影")
