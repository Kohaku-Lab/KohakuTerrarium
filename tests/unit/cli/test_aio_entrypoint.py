"""Unit tests for the ``kt-aio`` entrypoint helpers.

The ``run()`` function spawns subprocesses, so we can't unit-test it
end-to-end here.  We DO test every helper it composes
(_resolve_token / _persist_token / _wait_for_port / _kt_executable)
against the real filesystem and a real loopback socket.
"""

import socket
import threading
import time


from kohakuterrarium.cli import _aio_entrypoint as aio


class TestResolveToken:
    def test_token_from_file(self, tmp_path, monkeypatch):
        token_file = tmp_path / "tok"
        token_file.write_text("filetoken\n", encoding="utf-8")
        monkeypatch.setenv("KT_HOST_TOKEN_FILE", str(token_file))
        monkeypatch.delenv("KT_HOST_TOKEN", raising=False)
        assert aio._resolve_token(tmp_path) == "filetoken"

    def test_token_from_env_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KT_HOST_TOKEN_FILE", raising=False)
        monkeypatch.setenv("KT_HOST_TOKEN", "envtoken")
        assert aio._resolve_token(tmp_path) == "envtoken"

    def test_token_generated_when_neither_present(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KT_HOST_TOKEN_FILE", raising=False)
        monkeypatch.delenv("KT_HOST_TOKEN", raising=False)
        tok = aio._resolve_token(tmp_path)
        # secrets.token_hex(24) → 48 hex chars
        assert len(tok) == 48
        assert all(c in "0123456789abcdef" for c in tok)


class TestPersistToken:
    def test_persist_writes_file(self, tmp_path):
        path = aio._persist_token(tmp_path, "abc123")
        assert path.read_text(encoding="utf-8") == "abc123\n"
        assert path.name == "host-token"


class TestWaitForPort:
    def test_returns_true_when_port_already_listening(self):
        # Bind an ephemeral socket on loopback, then probe it.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            assert aio._wait_for_port("127.0.0.1", port, timeout_s=2.0) is True
        finally:
            s.close()

    def test_returns_false_when_port_never_opens(self):
        # An unbound port → connect refused → timeout returns False.
        # Pick a random high port unlikely to be bound.
        assert aio._wait_for_port("127.0.0.1", 1, timeout_s=0.3) is False

    def test_returns_true_when_port_opens_mid_wait(self):
        # Open the port from another thread half a second after the
        # call starts; the waiter should succeed.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        def _open_later():
            time.sleep(0.5)
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                srv.bind(("127.0.0.1", port))
                srv.listen(1)
                time.sleep(2.0)
            finally:
                srv.close()

        t = threading.Thread(target=_open_later, daemon=True)
        t.start()
        try:
            assert aio._wait_for_port("127.0.0.1", port, timeout_s=3.0) is True
        finally:
            t.join(timeout=3.5)


class TestKtExecutable:
    def test_returns_a_string_path(self):
        # On a real install the venv's ``kt`` script is present; on
        # CI without install it falls through to ``shutil.which`` or
        # the "kt" sentinel.  Either way the helper returns a string.
        out = aio._kt_executable()
        assert isinstance(out, str)
        assert out  # non-empty
