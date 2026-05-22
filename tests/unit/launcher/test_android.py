"""Unit tests for the Android launcher entry point.

The launcher's contract:

1. Hand off to ``start_uvicorn_with_port_fallback`` which only
   returns AFTER uvicorn has actually bound the socket.
2. Write the **verified-bound** port to ``KT_PORT_FILE`` atomically
   (Java's foreground service polls that file).
3. Block on the uvicorn server until shutdown.

These tests pin (2) end-to-end without booting a real server — we
fake ``start_uvicorn_with_port_fallback`` to return a stub server
+ a chosen port, then assert the file content + that the launcher
blocks until ``server.should_exit`` flips.
"""


import pytest

from kohakuterrarium.launcher import android as android_launcher


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "KT_PROFILE",
        "KT_CONFIG_DIR",
        "KT_SANDBOX_BIN_DIR",
        "KT_SANDBOX_ASSETS_DIR",
        "KT_PORT_FILE",
        "KT_SERVE_PORT",
    ):
        monkeypatch.delenv(key, raising=False)


class _StubServer:
    """Minimal stand-in for uvicorn.Server in tests."""

    def __init__(self):
        self.should_exit = False

    def stop_soon(self, delay_s: float = 0.05):
        # Trip ``should_exit`` from a sidecar thread so the
        # launcher's block-loop exits.
        import threading

        def _flip():
            import time as _t

            _t.sleep(delay_s)
            self.should_exit = True

        threading.Thread(target=_flip, daemon=True).start()


class TestWritePortFile:
    def test_writes_port_atomically(self, monkeypatch, tmp_path):
        target = tmp_path / "kohakuterrarium" / "port.txt"
        monkeypatch.setenv("KT_PORT_FILE", str(target))
        android_launcher._write_port_file(54321)
        assert target.read_text(encoding="utf-8").strip() == "54321"

    def test_creates_parent_dirs(self, monkeypatch, tmp_path):
        target = tmp_path / "deep" / "nested" / "port.txt"
        monkeypatch.setenv("KT_PORT_FILE", str(target))
        android_launcher._write_port_file(8080)
        assert target.is_file()

    def test_overwrites_existing(self, monkeypatch, tmp_path):
        target = tmp_path / "port.txt"
        target.write_text("99999\n", encoding="utf-8")
        monkeypatch.setenv("KT_PORT_FILE", str(target))
        android_launcher._write_port_file(1234)
        assert target.read_text(encoding="utf-8").strip() == "1234"

    def test_no_env_no_write(self, monkeypatch, tmp_path):
        monkeypatch.delenv("KT_PORT_FILE", raising=False)
        android_launcher._write_port_file(8080)  # no raise

    def test_partial_write_not_visible(self, monkeypatch, tmp_path):
        target = tmp_path / "port.txt"
        monkeypatch.setenv("KT_PORT_FILE", str(target))
        android_launcher._write_port_file(7777)
        assert not (tmp_path / "port.txt.part").exists()
        assert target.read_text(encoding="utf-8").strip() == "7777"


class TestServeAndReport:
    """Pin the post-bind handoff: the file MUST contain the port
    that ``start_uvicorn_with_port_fallback`` actually returned, not
    the port we asked for.  Audit fix — earlier code had a TOCTOU
    where a pre-bound port could disagree with uvicorn's actual
    bind."""

    def test_writes_actual_bound_port_not_requested(
        self, monkeypatch, tmp_path
    ):
        target = tmp_path / "port.txt"
        monkeypatch.setenv("KT_PORT_FILE", str(target))
        monkeypatch.setenv("KT_SERVE_PORT", "8001")
        stub = _StubServer()
        captured = {}

        def fake_start(app, *, requested_port, host, log_level):
            captured["requested"] = requested_port
            # Simulate uvicorn picking the NEXT port after a busy 8001
            # (the framework's "port fallback" behaviour).
            return stub, 8003

        # Patch the symbol the launcher imports.
        from kohakuterrarium.serving import web as web_mod

        monkeypatch.setattr(
            web_mod, "start_uvicorn_with_port_fallback", fake_start
        )
        # Also patch app creation so we don't actually build the
        # framework's app graph in this unit test.
        from kohakuterrarium.api import app as app_mod

        monkeypatch.setattr(
            app_mod, "create_app", lambda **kwargs: object()
        )

        stub.stop_soon(0.05)
        rc = android_launcher._serve_and_report()
        assert rc == 0
        assert captured["requested"] == 8001
        # Critical: the FILE reports the actually-bound port (8003)
        # not the requested one (8001).
        assert target.read_text(encoding="utf-8").strip() == "8003"

    def test_default_requested_port_8001_when_env_unset(
        self, monkeypatch, tmp_path
    ):
        target = tmp_path / "port.txt"
        monkeypatch.setenv("KT_PORT_FILE", str(target))
        stub = _StubServer()
        captured = {}

        def fake_start(app, *, requested_port, host, log_level):
            captured["requested"] = requested_port
            return stub, requested_port

        from kohakuterrarium.serving import web as web_mod
        from kohakuterrarium.api import app as app_mod

        monkeypatch.setattr(
            web_mod, "start_uvicorn_with_port_fallback", fake_start
        )
        monkeypatch.setattr(
            app_mod, "create_app", lambda **kwargs: object()
        )

        stub.stop_soon(0.05)
        android_launcher._serve_and_report()
        assert captured["requested"] == 8001

    def test_invalid_env_falls_back_to_default(self, monkeypatch, tmp_path):
        # Explicit "0" or empty env shouldn't silently bind 0
        # (which would mean "ephemeral"); we want a stable default
        # so retries / restarts converge on the same port.
        monkeypatch.setenv("KT_SERVE_PORT", "0")
        monkeypatch.setenv("KT_PORT_FILE", str(tmp_path / "p.txt"))
        stub = _StubServer()
        captured = {}

        def fake_start(app, *, requested_port, host, log_level):
            captured["requested"] = requested_port
            return stub, requested_port

        from kohakuterrarium.serving import web as web_mod
        from kohakuterrarium.api import app as app_mod

        monkeypatch.setattr(
            web_mod, "start_uvicorn_with_port_fallback", fake_start
        )
        monkeypatch.setattr(
            app_mod, "create_app", lambda **kwargs: object()
        )
        stub.stop_soon(0.05)
        android_launcher._serve_and_report()
        assert captured["requested"] == 8001


class TestMain:
    def test_uvicorn_exception_bubbles_to_exit_1(self, monkeypatch):
        from kohakuterrarium.serving import web as web_mod
        from kohakuterrarium.api import app as app_mod

        monkeypatch.setattr(
            app_mod, "create_app", lambda **kwargs: object()
        )

        def boom(app, **kwargs):
            raise RuntimeError("uvicorn refused to bind")

        monkeypatch.setattr(
            web_mod, "start_uvicorn_with_port_fallback", boom
        )
        rc = android_launcher.main()
        assert rc == 1

    def test_keyboard_interrupt_returns_0(self, monkeypatch):
        from kohakuterrarium.serving import web as web_mod
        from kohakuterrarium.api import app as app_mod

        monkeypatch.setattr(
            app_mod, "create_app", lambda **kwargs: object()
        )

        def interrupted(app, **kwargs):
            raise KeyboardInterrupt()

        monkeypatch.setattr(
            web_mod, "start_uvicorn_with_port_fallback", interrupted
        )
        rc = android_launcher.main()
        assert rc == 0


class TestPortFileRoundTrip:
    """Pin the contract Java reads: the file content is the port,
    a single integer + newline, UTF-8 encoded."""

    def test_format_matches_java_reader(self, monkeypatch, tmp_path):
        target = tmp_path / "port.txt"
        monkeypatch.setenv("KT_PORT_FILE", str(target))
        android_launcher._write_port_file(8001)
        # Java's reader is:
        #   Integer.parseInt(Files.readAllBytes(path).trim())
        raw = target.read_bytes()
        text = raw.decode("utf-8")
        assert text.strip() == "8001"
        assert int(text.strip()) == 8001
