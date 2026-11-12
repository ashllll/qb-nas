"""fnOS 桥接协议、持久任务身份与失败路径。"""

from __future__ import annotations

import json

import httpx
import pytest

from magnet_harvester.aria2_client import Aria2BridgeClient
from magnet_harvester.config import Aria2Config
from magnet_harvester.models import TaskStatus

HASH = "0123456789abcdef0123456789abcdef01234567"
MAGNET = f"magnet:?xt=urn:btih:{HASH}"
GID = "000000000000002a"


def make_client(tmp_path, handler, *, list_responses=False, **overrides):
    config = Aria2Config(
        rpc_url="http://nas.test:6800/jsonrpc",
        secret="test-secret",
        save_path="/vol3/1000/Download",
        state_db_path=str(tmp_path / "items.db"),
    )
    for key, value in overrides.items():
        setattr(config, key, value)

    def transport(request):
        method = json.loads(request.content)["method"]
        if not list_responses and method in {
            "aria2.tellActive",
            "aria2.tellWaiting",
            "aria2.tellStopped",
        }:
            return reply(request, [])
        return handler(request)

    client = Aria2BridgeClient(config)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    return client


def reply(request, result=None, error=None):
    body = json.loads(request.content)
    payload = {"jsonrpc": "2.0", "id": body["id"]}
    payload["error" if error else "result"] = error or result
    return httpx.Response(200, json=payload)


async def test_submit_persists_exact_gid_and_restart_syncs_without_uri(tmp_path):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if body["method"] == "aria2.addUri":
            return reply(request, GID)
        assert body["method"] == "aria2.tellStatus"
        return reply(
            request,
            {
                "gid": GID,
                "status": "active",
                "totalLength": "200",
                "completedLength": "50",
                "files": [{"uris": []}],
            },
        )

    client = make_client(tmp_path, handler)
    assert await client.add_magnet(MAGNET, "电影", "电影/2026")
    assert requests[0]["params"] == [
        "token:test-secret",
        [MAGNET],
        {"dir": "/vol3/1000/Download/电影/2026"},
    ]
    await client.close()
    client = make_client(tmp_path, handler)
    snapshot, removed = await client.poll_torrent_snapshot_with_removed()
    assert removed == set()
    assert snapshot[HASH]["download_state"] == {
        "status": TaskStatus.downloading,
        "progress": 25.0,
        "torrent_state": "aria2:active",
        "error_msg": None,
    }
    assert requests[-1]["params"] == ["token:test-secret", GID]
    assert await client.add_magnet(MAGNET, "电影")
    assert sum(r["method"] == "aria2.addUri" for r in requests) == 1
    await client.close()


@pytest.mark.parametrize(
    "status,total,done,expected,progress",
    [
        ("active", "0", "0", TaskStatus.downloading, 0),
        ("waiting", "100", "0", TaskStatus.queued, 0),
        ("paused", "100", "10", TaskStatus.paused, 10),
        ("complete", "0", "0", TaskStatus.success, 100),
        ("error", "100", "100", TaskStatus.error, 100),
    ],
)
async def test_remote_state_is_authoritative(tmp_path, status, total, done, expected, progress):
    def handler(request):
        if json.loads(request.content)["method"] == "aria2.addUri":
            return reply(request, GID)
        return reply(
            request,
            {
                "gid": GID,
                "status": status,
                "totalLength": total,
                "completedLength": done,
                "errorMessage": "disk full" if status == "error" else "",
            },
        )

    client = make_client(tmp_path, handler)
    assert await client.add_magnet(MAGNET, "电影")
    snapshot, removed = await client.poll_torrent_snapshot_with_removed()
    mapped = snapshot[HASH]["download_state"]
    assert mapped["status"] == expected
    assert mapped["progress"] == progress
    assert mapped["error_msg"] == ("disk full" if status == "error" else None)
    assert not removed
    await client.close()


async def test_transport_failure_is_not_task_removal(tmp_path):
    failed = False

    def handler(request):
        if json.loads(request.content)["method"] == "aria2.addUri":
            return reply(request, GID)
        if failed:
            return reply(request, error={"code": 1, "message": f"GID#{GID} is not found"})
        raise httpx.ConnectError("offline")

    client = make_client(tmp_path, handler)
    assert await client.add_magnet(MAGNET, "电影")
    with pytest.raises(RuntimeError, match="ConnectError"):
        await client.poll_torrent_snapshot_with_removed()
    failed = True
    assert await client.poll_torrent_snapshot_with_removed() == ({}, {HASH})
    await client.close()


async def test_timeout_persists_unknown_outcome_and_blocks_duplicate_submission(tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        raise httpx.ReadTimeout("test-secret was echoed")

    client = make_client(tmp_path, handler)
    assert not await client.add_magnet(MAGNET, "电影")
    assert "test-secret" not in client.last_error
    await client.close()
    client = make_client(tmp_path, handler)
    assert not await client.add_magnet(MAGNET, "电影")
    assert "核对" in client.last_error
    assert len(requests) == 1
    await client.close()


async def test_bindings_are_endpoint_scoped(tmp_path):
    def handler(request):
        return reply(request, GID)

    client = make_client(tmp_path, handler)
    assert await client.add_magnet(MAGNET, "电影")
    await client.close()
    client = make_client(tmp_path, handler, rpc_url="http://another.test:6800/jsonrpc")
    assert await client.poll_torrent_snapshot_with_removed() == ({}, set())
    await client.close()


@pytest.mark.parametrize(
    "path", ["../../etc", "/downloads/电影", "/vol3/../etc", "x\\..\\y", "x\0y"]
)
async def test_unsafe_or_docker_paths_never_submit(tmp_path, path):
    calls = []
    client = make_client(tmp_path, lambda r: calls.append(r))
    assert not await client.add_magnet(MAGNET, "电影", path)
    assert calls == []
    await client.close()


async def test_default_directory_comes_from_bridge(tmp_path):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return reply(
            request, {"dir": "/vol2/Download"} if body["method"] == "aria2.getGlobalOption" else GID
        )

    client = make_client(tmp_path, handler, save_path="")
    assert await client.add_magnet(MAGNET, "电影")
    assert requests[-1]["params"][-1] == {"dir": "/vol2/Download/电影"}
    await client.close()


async def test_ping_checks_fnos_backend_not_only_rpc_version(tmp_path):
    methods = []

    def handler(request):
        method = json.loads(request.content)["method"]
        methods.append(method)
        if method == "aria2.getVersion":
            return reply(
                request,
                {"version": "1.37.0", "enabledFeatures": ["bridged-to-fnos-downloadcenter"]},
            )
        return reply(request, error={"code": 1, "message": "NAS login failed test-secret"})

    client = make_client(tmp_path, handler)
    assert not await client.ping()
    assert methods == ["aria2.getVersion", "aria2.getGlobalStat"]
    assert "test-secret" not in client.last_error
    await client.close()


async def test_plain_aria2_is_rejected(tmp_path):
    client = make_client(tmp_path, lambda r: reply(r, {"version": "1.37", "enabledFeatures": []}))
    assert not await client.ping()
    assert "fnOS" in client.last_error
    await client.close()


@pytest.mark.parametrize("state", ["complete", "removed"])
async def test_terminal_snapshot_replays_after_restart_until_store_reconciles(tmp_path, state):
    calls = []

    def handler(request):
        method = json.loads(request.content)["method"]
        calls.append(method)
        return reply(request, GID if method == "aria2.addUri" else {"gid": GID, "status": state})

    client = make_client(tmp_path, handler)
    assert await client.add_magnet(MAGNET, "电影")
    snapshot = await client.poll_torrent_snapshot_with_removed()
    await client.close()
    client = make_client(tmp_path, handler)
    assert await client.poll_torrent_snapshot_with_removed() == snapshot
    assert calls.count("aria2.tellStatus") == 1
    await client.close()


async def test_config_normalization_precedes_binding_identity(tmp_path):
    client = make_client(tmp_path, lambda r: reply(r, GID), rpc_url="http://nas.test:6800/")
    assert await client.add_magnet(MAGNET, "电影")
    await client.close()
    client = make_client(tmp_path, lambda r: reply(r, {"gid": GID, "status": "active"}))
    snapshot, removed = await client.poll_torrent_snapshot_with_removed()
    assert HASH in snapshot
    assert not removed
    await client.close()


async def test_list_poll_matches_only_persisted_gid_and_avoids_per_task_queries(tmp_path):
    calls = []
    second_hash = "a" * 40

    def handler(request):
        body = json.loads(request.content)
        method = body["method"]
        calls.append(body)
        if method == "aria2.addUri":
            return reply(
                request, GID if second_hash not in body["params"][1][0] else "000000000000002b"
            )
        if method == "aria2.tellActive":
            return reply(
                request,
                [
                    {"gid": GID, "status": "active"},
                    {"gid": "000000000000002b", "status": "active"},
                    {"gid": "000000000000ffff", "status": "active"},
                ],
            )
        return reply(request, [])

    client = make_client(tmp_path, handler, list_responses=True)
    assert await client.add_magnet(MAGNET, "电影")
    assert await client.add_magnet(f"magnet:?xt=urn:btih:{second_hash}", "电影")
    snapshot, removed = await client.poll_torrent_snapshot_with_removed()
    assert set(snapshot) == {HASH, second_hash}
    assert not removed
    assert [c["method"] for c in calls[2:]] == [
        "aria2.tellActive",
        "aria2.tellWaiting",
        "aria2.tellStopped",
    ]
    assert calls[3]["params"] == ["token:test-secret", 0, 100000]
    await client.close()


async def test_explicit_auth_rejection_does_not_leave_unknown_submission(tmp_path):
    reject = True

    def handler(request):
        return (
            reply(request, error={"code": 1, "message": "Token 验证失败"})
            if reject
            else reply(request, GID)
        )

    client = make_client(tmp_path, handler)
    assert not await client.add_magnet(MAGNET, "电影")
    reject = False
    assert await client.add_magnet(MAGNET, "电影")
    await client.close()


async def test_cancelled_submission_remains_pending_after_restart(tmp_path):
    import asyncio

    from magnet_harvester.utils.bg_tasks import BGTaskManager

    entered = asyncio.Event()
    requests = []

    async def handler(request):
        requests.append(request)
        entered.set()
        await asyncio.Event().wait()

    client = make_client(tmp_path, lambda r: reply(r, GID))
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    task = BGTaskManager.spawn(client.add_magnet(MAGNET, "电影"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await client.close()
    client = make_client(tmp_path, lambda r: requests.append(r))
    assert not await client.add_magnet(MAGNET, "电影")
    assert "核对" in client.last_error
    assert len(requests) == 1
    await client.close()


async def test_invalid_config_never_contacts_backend(tmp_path):
    requests = []
    client = make_client(
        tmp_path, lambda r: requests.append(r), rpc_url="http://user:password@nas.test/jsonrpc"
    )
    assert not await client.ping()
    assert not await client.add_magnet(MAGNET, "电影")
    assert requests == []
    assert "password" not in client.last_error
    await client.close()


async def test_wrong_gid_response_is_not_applied_to_magnet(tmp_path):
    def handler(request):
        return reply(
            request,
            GID
            if json.loads(request.content)["method"] == "aria2.addUri"
            else {
                "gid": "000000000000ffff",
                "status": "complete",
            },
        )

    client = make_client(tmp_path, handler)
    assert await client.add_magnet(MAGNET, "电影")
    with pytest.raises(RuntimeError, match="身份不匹配"):
        await client.poll_torrent_snapshot_with_removed()
    await client.close()


@pytest.mark.parametrize(
    "magnet",
    [
        f"magnet:?dn=urn:btih:{HASH}",
        f"magnet:?xt=urn:btih:{HASH}extra",
        f"magnet:?xt=urn:btih:{HASH}&xt=urn:btih:{'a' * 40}",
    ],
)
async def test_magnet_identity_requires_one_complete_xt(tmp_path, magnet):
    requests = []
    client = make_client(tmp_path, lambda r: requests.append(r))
    assert not await client.add_magnet(magnet, "电影")
    assert requests == []
    await client.close()


async def test_completed_task_deleted_in_nas_can_be_submitted_again(tmp_path):
    deleted = False
    adds = []

    def handler(request):
        method = json.loads(request.content)["method"]
        if method == "aria2.addUri":
            adds.append(method)
            return reply(request, GID)
        if deleted:
            return reply(request, error={"code": 1, "message": f"GID#{GID} is not found"})
        return reply(request, {"gid": GID, "status": "complete"})

    client = make_client(tmp_path, handler)
    assert await client.add_magnet(MAGNET, "电影")
    await client.poll_torrent_snapshot_with_removed()
    deleted = True
    assert await client.add_magnet(MAGNET, "电影")
    assert len(adds) == 2
    await client.close()


async def test_presend_connection_failure_allows_retry(tmp_path):
    offline = True

    def handler(request):
        if offline:
            raise httpx.ConnectError("connection refused")
        return reply(request, GID)

    client = make_client(tmp_path, handler)
    assert not await client.add_magnet(MAGNET, "电影")
    offline = False
    assert await client.add_magnet(MAGNET, "电影")
    await client.close()


@pytest.mark.parametrize("late_state", ["removed", "complete"])
async def test_stale_terminal_poll_cannot_affect_new_gid(tmp_path, late_state):
    gid = GID
    replacing = False
    replaced = False
    client = None

    async def handler(request):
        nonlocal gid, replacing, replaced
        method = json.loads(request.content)["method"]
        if method == "aria2.addUri":
            if replacing:
                gid = "000000000000002b"
            return reply(request, gid)
        if replaced:
            return reply(request, {"gid": gid, "status": "active"})
        if not replacing:
            replacing = True
            assert await client.add_magnet(MAGNET, "电影")
            replaced = True
        if replaced:
            return reply(request, {"gid": GID, "status": late_state})
        return reply(request, error={"code": 1, "message": f"GID#{GID} is not found"})

    client = make_client(tmp_path, handler)
    assert await client.add_magnet(MAGNET, "电影")
    assert await client.poll_torrent_snapshot_with_removed() == ({}, set())
    snapshot, removed = await client.poll_torrent_snapshot_with_removed()
    assert not removed
    assert snapshot[HASH]["download_state"]["status"] == TaskStatus.downloading
    await client.close()
