"""Source desktop startup keeps packaged-runtime behavior opt-in."""

import json
from types import SimpleNamespace

import pytest

from kohakuterrarium.serving.desktop import server


def test_source_starts_without_built_assets_on_exact_loopback_port(
    monkeypatch, tmp_path
):
    app = SimpleNamespace(state=SimpleNamespace())
    calls = []
    monkeypatch.setattr(server, "WEB_DIST_DIR", tmp_path / "missing")
    monkeypatch.setattr(server, "create_app", lambda **kw: calls.append(kw) or app)
    monkeypatch.setattr(
        server,
        "start_uvicorn_with_port_fallback",
        lambda instance, **kw: calls.append((instance, kw))
        or (instance, kw["requested_port"]),
    )
    assert server.start_desktop_server(18001, "ERROR", source=True) == (app, 18001)
    assert calls[0]["static_dir"] is None
    assert calls[1][1]["host"] == "127.0.0.1"
    assert calls[1][1]["max_tries"] == 1
    assert app.state.desktop_source is True
    with pytest.raises(RuntimeError, match="web_dist not found"):
        server.start_desktop_server(18001, "ERROR")


def test_source_child_publishes_ready_without_refreshing_installed_commands(
    monkeypatch, tmp_path
):
    instance = SimpleNamespace(should_exit=True)
    monkeypatch.setattr(
        server, "start_desktop_server", lambda *a, source: (instance, 18001)
    )
    monkeypatch.setattr(
        server, "_refresh_shims", lambda: pytest.fail("modified installed commands")
    )
    state = tmp_path / "state.json"
    assert server.run_server_child(18001, "ERROR", state, source=True) == 0
    assert json.loads(state.read_text()) == {"status": "ready", "port": 18001}


def test_source_child_publishes_startup_failure(monkeypatch, tmp_path):
    def occupied(*args, **kwargs):
        raise RuntimeError("port in use")

    monkeypatch.setattr(server, "start_desktop_server", occupied)
    state = tmp_path / "state.json"
    assert server.run_server_child(18001, "ERROR", state, source=True) == 1
    assert json.loads(state.read_text()) == {"status": "error", "error": "port in use"}
