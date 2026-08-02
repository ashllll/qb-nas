from pathlib import Path


def test_app_mounts_static_assets():
    from starlette.routing import Mount

    from magnet_harvester.main import app

    static_mounts = [
        route for route in app.routes if isinstance(route, Mount) and route.path == "/static"
    ]
    assert len(static_mounts) == 1


def test_qbit_password_is_persisted_without_frontend_echo():
    html = Path("static/index.html").read_text(encoding="utf-8")
    app_js = Path("static/app.js").read_text(encoding="utf-8")
    assert 'placeholder="留空保持已保存密码"' in html
    assert "密码仅保存在服务端，不会回显" in html
    assert "qbit_password_configured" in app_js
    assert "if (password) payload.qbit_password = password;" in app_js


def test_frontend_throttles_transient_qbit_logs_but_keeps_real_failures():
    app_js = Path("static/app.js").read_text(encoding="utf-8")
    assert "LOG_DEDUPE_WINDOW_MS" in app_js
    assert '"qbit-transient-retry"' in app_js
    assert "qB 状态暂时异常，正在重试" in app_js
    assert "下载失败 · ${name} · ${msg.error_msg}" in app_js
