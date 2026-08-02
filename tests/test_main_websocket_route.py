"""Test main app mounts websocket endpoint from api.websocket."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import magnet_harvester.main as main_module


def _mounted_routes():
    for route in main_module.app.routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from included.routes
        else:
            yield route


def test_main_app_ws_route_is_mounted_from_api_websocket():
    ws_routes = [route for route in _mounted_routes() if getattr(route, "path", None) == "/ws"]

    assert len(ws_routes) == 1
    assert ws_routes[0].endpoint.__module__ == "magnet_harvester.api.websocket"


def test_main_app_index_route_is_not_mounted_from_api_websocket():
    index_routes = [route for route in _mounted_routes() if getattr(route, "path", None) == "/"]

    assert len(index_routes) == 1
    assert index_routes[0].endpoint.__module__ != "magnet_harvester.api.websocket"
