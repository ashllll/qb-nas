def test_app_mounts_static_assets():
    from starlette.routing import Mount

    from magnet_harvester.main import app

    static_mounts = [
        route for route in app.routes if isinstance(route, Mount) and route.path == "/static"
    ]
    assert len(static_mounts) == 1
