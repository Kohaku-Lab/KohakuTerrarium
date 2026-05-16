"""Unit tests for :mod:`kohakuterrarium.serving.web`.

Only the testable helper functions are covered here. The ``run_web_server``
and ``run_desktop_app`` paths drive uvicorn / pywebview and are
end-user-facing UI / platform-dependent — they fall under the
"final end-user UI" exception in the coverage policy.
"""

import json
import socket

import pytest

from kohakuterrarium.serving.web import (
    _publish_actual_port,
    _resolve_config_dirs,
    find_free_port,
)

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
