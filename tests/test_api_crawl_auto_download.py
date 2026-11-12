"""生产 ingest 装配下 POST /api/crawl 对 auto_download 的契约。

采集服务链路只入库、不下载；生产装配（ctx.app_services.ingest 非空）里
auto_download=true 必须 422，而不是 200 建一个不会下载的任务。
"""

from __future__ import annotations

from magnet_harvester.api.error_contract import install_error_handlers
from tests.fixtures import asgi_client, make_test_app


class FakeIngest:
    def __init__(self):
        self.calls: list[tuple[str, str, int]] = []

    async def ingest_url(self, url, *, source="ui", depth=1, **kwargs):
        self.calls.append((url, source, depth))
        return {"ok": True, "job_id": "job-1", "reason": ""}


def _app_with_ingest():
    app, ctx, _ = make_test_app()
    install_error_handlers(app)
    ingest = FakeIngest()
    ctx.app_services.ingest = ingest
    return app, ingest


def test_ingest_branch_rejects_auto_download():
    app, ingest = _app_with_ingest()

    with asgi_client(app) as client:
        response = client.post(
            "/api/crawl",
            json={"url": "https://example.com/source", "auto_download": True},
        )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "auto_download_not_supported"
    assert "自动下载" in body["detail"]
    assert "auto_download" in body["hint"]
    assert ingest.calls == []


def test_ingest_branch_accepts_false_and_omitted_auto_download():
    app, ingest = _app_with_ingest()

    with asgi_client(app) as client:
        omitted = client.post("/api/crawl", json={"url": "https://example.com/a"})
        explicit_false = client.post(
            "/api/crawl",
            json={"url": "https://example.com/b", "auto_download": False},
        )

    assert omitted.status_code == 200
    assert omitted.json()["job_id"] == "job-1"
    assert explicit_false.status_code == 200
    assert [call[0] for call in ingest.calls] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert {call[1] for call in ingest.calls} == {"ui"}
