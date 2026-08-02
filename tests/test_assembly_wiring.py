"""Verify build_runtime() wires all components correctly."""

import pytest


@pytest.mark.asyncio
async def test_build_runtime_wires_all_components(monkeypatch):
    import magnet_harvester.assembly as assembly_module
    from magnet_harvester.config import CrawlerConfig, QBitConfig

    class FakeCrawler:
        def __init__(self, config, site_auth=None, tavily=None, task_manager=None):
            assert isinstance(config, CrawlerConfig)
            self._config = config
            self.site_auth = site_auth
            self.max_depth = 3

        async def start(self):
            pass

        async def stop(self):
            pass

    class FakeQbit:
        def __init__(self, config):
            assert isinstance(config, QBitConfig)
            self._config = config
            self.host = config.host
            self.username = config.username
            self.closed = False

        async def ping(self):
            return True

        async def close(self):
            self.closed = True

        def get_stats(self):
            return {}

    monkeypatch.setattr(assembly_module, "MagnetCrawler", FakeCrawler)
    monkeypatch.setattr(assembly_module, "QBittorrentClient", FakeQbit)

    runtime = assembly_module.build_runtime()
    ctx = runtime.ctx

    # Every slot must be populated
    assert ctx.store is not None, "store missing"
    assert ctx.bus is not None, "bus missing"
    assert ctx.pipeline is not None, "pipeline missing"
    assert ctx.crawler is not None, "crawler missing"
    assert ctx.classifier is not None, "classifier missing"
    assert ctx.qbit is not None, "qbit missing"
    assert ctx.stats is not None, "stats missing"
    assert ctx.bg_manager is not None, "bg_manager missing"
    assert ctx.broadcaster is not None, "broadcaster missing"
    assert ctx.action_executor is not None, "action_executor missing"
    assert ctx.qbit_sync is not None, "qbit_sync missing"
    assert ctx.qbit_lock is not None, "qbit_lock missing"
    assert ctx.error_handler is not None, "error_handler missing"
    assert ctx.observability is not None, "observability missing"
    assert ctx.item_queries is not None, "item_queries missing"
    assert ctx.api_key is not None, "api_key missing"

    # SyncLoop must be wired
    assert runtime.sync_loop is not None, "sync_loop missing"

    # Config values accessible via injected qbit
    assert ctx.qbit.host is not None
    assert ctx.qbit.username is not None


def test_build_runtime_uses_configured_qbit_sync_interval(monkeypatch):
    import magnet_harvester.assembly as assembly_module

    monkeypatch.setattr(assembly_module.settings, "QBIT_SYNC_INTERVAL", 7.5, raising=False)

    runtime = assembly_module.build_runtime()

    assert runtime.sync_loop._backoff.next_delay() == 7.5
