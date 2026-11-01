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


class FakeSubmissionGateway:
    def __init__(
        self,
        *,
        request=None,
        ensure_category=None,
        get_base_save_path=None,
        find_torrent_by_prefix=None,
    ):
        self.request = request or AsyncMock(return_value=_ok_response())
        self.ensure_category = ensure_category or AsyncMock(return_value=True)
        self.get_base_save_path = get_base_save_path or AsyncMock(return_value="/downloads")
        self.find_torrent_by_prefix = find_torrent_by_prefix or AsyncMock(return_value=None)


class FakeSubmissionRecorder:
    def __init__(self):
        self.attempts = 0
        self.successes = 0
        self.failures = 0
        self.errors = []

    def attempted(self) -> None:
        self.attempts += 1

    def succeeded(self) -> None:
        self.successes += 1

    def failed(self) -> None:
        self.failures += 1

    def error(self, message: str | None) -> None:
        self.errors.append(message)


def _make_submitter(**overrides):
    gateway = overrides.get("gateway") or FakeSubmissionGateway(
        request=overrides.get("request"),
        ensure_category=overrides.get("ensure_category"),
        get_base_save_path=overrides.get("get_base_save_path"),
        find_torrent_by_prefix=overrides.get("find_torrent_by_prefix"),
    )
    return MagnetSubmitter(
        gateway=gateway,
        fs_base_path=overrides.get("fs_base_path", ""),
        recorder=overrides.get("recorder"),
    )


@pytest.mark.asyncio
async def test_submitter_succeeds_on_ok_response():
    request = AsyncMock(return_value=_ok_response())
    recorder = FakeSubmissionRecorder()

    submitter = _make_submitter(
        request=request,
        recorder=recorder,
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
    assert kwargs["data"]["autoTMM"] == "true"
    # 点「下载」即开始：不能再用 paused/stopped 把种子停在已停止状态
    assert "paused" not in kwargs["data"]
    assert "stopped" not in kwargs["data"]
    assert "savepath" not in kwargs["data"]
    assert recorder.attempts == 1
    assert recorder.successes == 1


@pytest.mark.asyncio
async def test_submitter_fails_on_invalid_magnet():
    recorder = FakeSubmissionRecorder()

    submitter = _make_submitter(
        recorder=recorder,
    )

    assert not await submitter.add_magnet("not-a-magnet", "电影", "")
    assert recorder.failures == 1
    assert recorder.errors and "btih" in recorder.errors[-1]


@pytest.mark.asyncio
async def test_submitter_uses_parser_btih_validation_rules():
    request = AsyncMock(return_value=_ok_response())
    recorder = FakeSubmissionRecorder()
    submitter = _make_submitter(
        request=request,
        recorder=recorder,
    )

    assert not await submitter.add_magnet("magnet:?xt=urn:btih:12345678", "电影", "")
    assert await submitter.add_magnet(
        "magnet:?xt=urn:btih:ABCDEFGHIJKLMNOPQRSTUVWXYZ234567",
        "电影",
        "/downloads/电影",
    )

    assert request.await_count == 1
    assert recorder.failures == 1
    assert recorder.successes == 1


@pytest.mark.asyncio
async def test_submitter_treats_duplicate_as_success():
    request = AsyncMock(return_value=_fail_response())
    find = AsyncMock(return_value={"hash": "01234567", "name": "Existing Movie"})
    recorder = FakeSubmissionRecorder()

    submitter = _make_submitter(
        request=request,
        find_torrent_by_prefix=find,
        recorder=recorder,
    )

    assert await submitter.add_magnet(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        "电影",
        "/downloads/电影",
    )

    find.assert_awaited_once_with("01234567")
    assert recorder.successes == 1


@pytest.mark.asyncio
async def test_submitter_fails_when_qb_rejects_and_no_duplicate():
    request = AsyncMock(return_value=_fail_response("Unknown error"))
    recorder = FakeSubmissionRecorder()

    submitter = _make_submitter(
        request=request,
        recorder=recorder,
    )

    assert not await submitter.add_magnet(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        "电影",
        "/downloads/电影",
    )

    assert recorder.failures == 1
    assert recorder.errors and "qB 拒绝" in recorder.errors[-1]


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


@pytest.mark.asyncio
async def test_submitter_rejects_relative_save_path_escape():
    request = AsyncMock(return_value=_ok_response())
    ensure = AsyncMock(return_value=True)
    recorder = FakeSubmissionRecorder()

    submitter = _make_submitter(
        request=request,
        ensure_category=ensure,
        get_base_save_path=AsyncMock(return_value="/volume1/downloads"),
        recorder=recorder,
    )

    assert not await submitter.add_magnet(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        "电影",
        "../escape",
    )

    ensure.assert_not_awaited()
    request.assert_not_awaited()
    assert recorder.failures == 1
    assert recorder.errors and "保存路径" in recorder.errors[-1]


@pytest.mark.asyncio
async def test_submitter_accepts_qbittorrent_v5_json_add_response():
    """qB v5 /torrents/add 返回 JSON（而非 v4 的 "Ok."）。"""
    response = httpx.Response(
        200,
        json={
            "added_torrent_ids": ["0123456789abcdef0123456789abcdef01234567"],
            "failure_count": 0,
            "pending_count": 0,
            "success_count": 1,
        },
        request=httpx.Request("POST", "http://qbit.test/api/v2/torrents/add"),
    )
    request = AsyncMock(return_value=response)
    recorder = FakeSubmissionRecorder()
    submitter = _make_submitter(request=request, recorder=recorder)

    assert await submitter.add_magnet(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567", "电影", ""
    )
    assert recorder.successes == 1


@pytest.mark.asyncio
async def test_submitter_detects_existing_torrent_when_magnet_uses_base32_btih():
    """base32 写法的 btih：查 qB 已有种子必须用归一化后的 hex 前缀。"""
    import base64

    from magnet_harvester.utils.infohash import normalize_infohash

    hex_hash = "0123456789abcdef0123456789abcdef01234567"
    base32 = base64.b32encode(bytes.fromhex(hex_hash)).decode()
    magnet = f"magnet:?xt=urn:btih:{base32}&dn=Base32"

    find_torrent = AsyncMock(return_value={"name": "already-there"})
    submitter = _make_submitter(
        request=AsyncMock(return_value=_fail_response()),
        find_torrent_by_prefix=find_torrent,
    )

    assert await submitter.add_magnet(magnet, "电影", "/downloads/电影")
    prefix = find_torrent.await_args.args[0]
    assert prefix == normalize_infohash(hex_hash)[:8]
