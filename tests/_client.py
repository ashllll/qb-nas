"""Warning-free TestClient replacement using httpx.ASGITransport."""

from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from typing import Any, Generator

import httpx
from fastapi import FastAPI


class _TestClientWrapper:
    """Sync facade over httpx.AsyncClient for ASGI apps."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._client = client
        self.app = app
        self._loop = loop

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._loop.run_until_complete(self._client.get(url, **kwargs))

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._loop.run_until_complete(self._client.post(url, **kwargs))

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._loop.run_until_complete(self._client.put(url, **kwargs))

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._loop.run_until_complete(self._client.delete(url, **kwargs))


@contextmanager
def asgi_client(
    app: FastAPI,
    base_url: str = "http://testserver",
) -> Generator[_TestClientWrapper, None, None]:
    """Create a synchronous test client for a FastAPI/ASGI application.

    Unlike the framework-provided synchronous test client, this helper uses
    ``httpx.ASGITransport`` directly and avoids the Starlette compatibility
    compatibility layer that emits deprecation warnings.
    """
    loop = asyncio.new_event_loop()
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(base_url=base_url, transport=transport)
    lifespan = app.router.lifespan_context(app)
    try:
        loop.run_until_complete(lifespan.__aenter__())
        yield _TestClientWrapper(client, app, loop)
    except BaseException:
        loop.run_until_complete(lifespan.__aexit__(*sys.exc_info()))
        raise
    else:
        loop.run_until_complete(lifespan.__aexit__(None, None, None))
    finally:
        loop.run_until_complete(client.aclose())
        loop.close()
