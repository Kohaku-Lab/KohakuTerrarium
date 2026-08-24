"""Tests for CLI run and session-selection helpers."""

from kohakuterrarium.cli import run


class _FailingPreviewStore:
    def __init__(self) -> None:
        self.closed = False

    def load_meta(self) -> dict:
        raise RuntimeError("broken metadata")

    def close(self) -> None:
        self.closed = True


class TestSessionPreview:
    def test_store_closes_when_metadata_loading_fails(
        self, monkeypatch, tmp_path
    ) -> None:
        store = _FailingPreviewStore()
        monkeypatch.setattr(run.SessionStore, "open_readonly", lambda _path: store)

        assert run._session_preview(tmp_path / "broken.kohakutr") == ""
        assert store.closed is True


class TestSessionDir:
    def test_explicit_session_dir_has_precedence(self, monkeypatch, tmp_path):
        explicit = tmp_path / "explicit"
        monkeypatch.setenv("KT_SESSION_DIR", str(explicit))
        monkeypatch.setattr(run, "_SESSION_DIR", tmp_path / "patched-default")

        assert run._session_dir() == explicit

    def test_config_dir_supplies_default_root(self, monkeypatch, tmp_path):
        monkeypatch.delenv("KT_SESSION_DIR", raising=False)
        monkeypatch.setattr(
            run,
            "_SESSION_DIR",
            run.Path.home() / ".kohakuterrarium" / "sessions",
        )
        monkeypatch.setattr(run, "config_dir", lambda: tmp_path / "config")

        assert run._session_dir() == tmp_path / "config" / "sessions"
