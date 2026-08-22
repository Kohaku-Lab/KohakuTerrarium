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
