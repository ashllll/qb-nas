from pathlib import Path


HTML = Path("static/index.html").read_text(encoding="utf-8")


def test_frontend_includes_all_supported_categories():
    for category in ["电影", "电视剧", "动漫", "音乐", "游戏", "软件", "综艺", "纪录片", "其他"]:
        assert f'data-cat="{category}"' in HTML


def test_frontend_write_requests_can_send_api_key():
    assert 'headers["X-API-Key"] = key' in HTML
    assert 'sessionStorage.getItem("magnet-api-key")' in HTML
    assert 'id="apiKeyInput"' in HTML
    assert '.getElementById("apiKeyInput")' in HTML
    assert '.addEventListener("input"' in HTML


def test_qbit_config_does_not_overwrite_password_with_blank_value():
    assert "if (password) payload.qbit_password = password;" in HTML


def test_websocket_init_replaces_stale_client_state():
    init_case = HTML.split('case "init":', 1)[1].split('case "crawl_start":', 1)[0]
    assert "items.clear();" in init_case
    assert "selected.clear();" in init_case


def test_bulk_selection_is_scoped_to_visible_items():
    select_all = HTML.split("function selectAllVisible()", 1)[1].split("function selectNone()", 1)[
        0
    ]
    toggle_all = HTML.split("function toggleVisible(checked)", 1)[1].split(
        "function selectAllVisible()", 1
    )[0]
    assert "visibleItems()" in select_all
    assert "visibleItems()" in toggle_all


def test_frontend_assets_are_local_and_mobile_navigation_exists():
    assert 'class="mobile-nav"' in HTML
    assert "fonts.googleapis.com" not in HTML
    assert "macos-coast-wallpaper" not in HTML
    assert "lucide.min.js" not in HTML
    assert "ICON_SYMBOLS" in HTML


def test_app_mounts_static_assets():
    source = Path("magnet_harvester/main.py").read_text(encoding="utf-8")
    assert 'app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")' in source
