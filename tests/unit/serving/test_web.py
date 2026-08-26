"""Unit tests for :mod:`kohakuterrarium.serving.web`.

Only the testable helper functions are covered here. The ``run_web_server``
and ``run_desktop_app`` paths drive uvicorn / pywebview and are
end-user-facing UI / platform-dependent — they fall under the
"final end-user UI" exception in the coverage policy.
"""

import json
import socket
import sys

import pytest

import kohakuterrarium.serving.web as web_mod
from kohakuterrarium.serving.web import (
    _publish_actual_port,
    _resolve_config_dirs,
    _run_desktop_app_blocking,
    find_free_port,
)

# ── startup trace ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_lifespan_records_ready_boundary(monkeypatch):
    from kohakuterrarium.api import app as api_app

    milestones = []

    async def _shutdown():
        return None

    engine = type(
        "Engine",
        (),
        {
            "_runtime_prompt": type(
                "Prompt", (), {"attach": lambda self: None, "detach": lambda self: None}
            )(),
            "shutdown": staticmethod(_shutdown),
        },
    )()
    monkeypatch.setattr(api_app, "get_aggregator", lambda: None)
    monkeypatch.setattr(api_app, "ensure_auth_migrated", lambda: None)
    monkeypatch.setattr(api_app, "get_engine", lambda: engine)
    monkeypatch.setattr(api_app, "close_session_index", lambda: None)
    monkeypatch.delenv("KT_STARTUP_SURFACE", raising=False)
    monkeypatch.setattr(
        api_app,
        "mark_startup",
        lambda event, **fields: milestones.append((event, fields)),
        raising=False,
    )
    app = type(
        "App",
        (),
        {"state": type("State", (), {"lab_mode": "standalone", "engine_pool": None})()},
    )()

    async with api_app.lifespan(app):
        assert milestones == [("api_lifespan_ready", {"surface": "web"})]


def test_web_server_records_ready_boundary(monkeypatch):
    milestones = []
    app = object()
    monkeypatch.setattr(web_mod, "configure_utf8_stdio", lambda **_kwargs: None)
    monkeypatch.setattr(web_mod, "set_level", lambda _level: None)
    monkeypatch.setattr(web_mod, "enable_stderr_logging", lambda _level: None)
    monkeypatch.setattr(web_mod, "_resolve_config_dirs", lambda: (["c"], ["t"]))
    monkeypatch.setattr(web_mod, "create_app", lambda **_kwargs: app)
    monkeypatch.setattr(web_mod, "find_free_port", lambda **_kwargs: 8123)
    monkeypatch.setattr(web_mod, "_publish_actual_port", lambda *_args: None)
    monkeypatch.setattr(web_mod.uvicorn, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        web_mod,
        "mark_startup",
        lambda event, **fields: milestones.append((event, fields)),
        raising=False,
    )

    web_mod.run_web_server(port=8001, dev=True, log_level="ERROR")

    assert milestones == [
        (
            "web_config_dirs_resolved",
            {"surface": "web", "creatures": 1, "terrariums": 1},
        ),
        ("web_app_created", {"surface": "web"}),
        ("web_server_run", {"surface": "web", "host": "127.0.0.1", "port": 8123}),
    ]


# ── desktop logging ─────────────────────────────────────────────


def test_desktop_blocking_ensures_file_logging(monkeypatch):
    calls = []
    monkeypatch.setattr(web_mod, "configure_utf8_stdio", lambda **kwargs: None)
    monkeypatch.setattr(web_mod, "enable_file_logging", lambda: calls.append("file"))
    monkeypatch.setattr(web_mod, "set_level", lambda level: None)
    monkeypatch.setattr(web_mod, "enable_stderr_logging", lambda level: None)
    monkeypatch.setitem(sys.modules, "webview", None)

    with pytest.raises(SystemExit):
        _run_desktop_app_blocking()

    assert calls == ["file"]


# ── find_free_port ──────────────────────────────────────────────


class TestFindFreePort:
    def test_returns_a_port(self):
        port = find_free_port(start=49152, max_tries=10)
        assert 49152 <= port <= 49152 + 9
        # Verify it's actually free (we can re-bind it).
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))

    def test_raises_when_no_port_free(self, monkeypatch):
        # Force every bind to fail.
        original_socket = socket.socket

        class _BlockedSocket(original_socket):
            def bind(self, addr):
                raise OSError("nope")

        monkeypatch.setattr(socket, "socket", _BlockedSocket)
        with pytest.raises(RuntimeError, match="No free port"):
            find_free_port(start=12345, max_tries=3)


# ── _publish_actual_port ────────────────────────────────────────


class TestPublishActualPort:
    def test_no_state_path_no_op(self):
        # No-op — should not raise.
        _publish_actual_port(None, "127.0.0.1", 8001)

    def test_missing_file_no_op(self, tmp_path):
        # File path provided but doesn't exist → no-op.
        _publish_actual_port(str(tmp_path / "absent.json"), "127.0.0.1", 8001)

    def test_updates_existing_state(self, tmp_path):
        state = tmp_path / "state.json"
        state.write_text(json.dumps({"pid": 123, "bound": False}))
        _publish_actual_port(str(state), "127.0.0.1", 8042)
        data = json.loads(state.read_text())
        assert data["port"] == 8042
        assert data["url"] == "http://127.0.0.1:8042"
        assert data["bound"] is True
        # Existing keys preserved.
        assert data["pid"] == 123

    def test_non_dict_content_skipped(self, tmp_path):
        state = tmp_path / "state.json"
        state.write_text("[1, 2, 3]")  # list not dict
        # Should not crash.
        _publish_actual_port(str(state), "127.0.0.1", 9000)

    def test_unreadable_file_no_crash(self, tmp_path):
        state = tmp_path / "state.json"
        state.write_text("not-json{")
        # Should not raise — defensive except.
        _publish_actual_port(str(state), "127.0.0.1", 9000)


# ── _resolve_config_dirs ────────────────────────────────────────


class TestResolveConfigDirs:
    def test_env_var_creatures(self, tmp_path, monkeypatch):
        c1 = tmp_path / "c1"
        c1.mkdir()
        c2 = tmp_path / "c2"
        c2.mkdir()
        monkeypatch.setenv("KT_CREATURES_DIRS", f"{c1},{c2}")
        monkeypatch.delenv("KT_TERRARIUMS_DIRS", raising=False)
        creatures, _ = _resolve_config_dirs()
        assert str(c1) in creatures
        assert str(c2) in creatures

    def test_env_var_terrariums(self, tmp_path, monkeypatch):
        t1 = tmp_path / "t1"
        t1.mkdir()
        monkeypatch.delenv("KT_CREATURES_DIRS", raising=False)
        monkeypatch.setenv("KT_TERRARIUMS_DIRS", str(t1))
        _, terrariums = _resolve_config_dirs()
        assert str(t1) in terrariums

    def test_no_env_vars(self, monkeypatch):
        monkeypatch.delenv("KT_CREATURES_DIRS", raising=False)
        monkeypatch.delenv("KT_TERRARIUMS_DIRS", raising=False)
        creatures, terrariums = _resolve_config_dirs()
        # Returns lists (possibly with packages + project dirs).
        assert isinstance(creatures, list)
        assert isinstance(terrariums, list)
