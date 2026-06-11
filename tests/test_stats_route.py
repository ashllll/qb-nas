"""
Test main app now mounts REST endpoints from api/routes.py.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import magnet_harvester.main as main_module

def test_main_app_stats_route_is_mounted_from_api_routes():
    stats_routes = [
        route for route in main_module.app.routes
        if getattr(route, "path", None) == "/api/stats"
    ]

    assert len(stats_routes) == 1
    assert stats_routes[0].endpoint.__module__ == "magnet_harvester.api.routes"


if __name__ == "__main__":
    test_main_app_stats_route_is_mounted_from_api_routes()
    print("=== stats route tests passed! ===")
