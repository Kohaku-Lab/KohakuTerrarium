"""Tests for CLI run and session-selection helpers."""

import pytest

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


class TestRunStartupTrace:
    @pytest.mark.asyncio
    async def test_records_engine_and_surface_milestones(self, monkeypatch, tmp_path):
        milestones = []
        creature = type("Creature", (), {"creature_id": "focus", "graph_id": "graph"})()

        class _Engine:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def add_creature(self, *_args, **_kwargs):
                return creature

        async def _run_cli(_engine, creature_id, _store):
            assert creature_id == "focus"

        monkeypatch.setattr(run._drive_settings, "resolve_drive_kwargs", lambda: {})
        monkeypatch.setattr(run, "Terrarium", lambda **_kwargs: _Engine())
        monkeypatch.setattr(run, "_looks_like_recipe", lambda _path: False)
        monkeypatch.setattr(
            run, "_attach_session_store", lambda *_args, **_kwargs: None
        )
        monkeypatch.setattr(
            run, "_apply_cli_topology", lambda *_args, **_kwargs: _async_none()
        )
        monkeypatch.setattr(run, "run_engine_with_rich_cli", _run_cli)
        monkeypatch.setattr(
            run,
            "mark_startup",
            lambda event, **fields: milestones.append((event, fields)),
            raising=False,
        )
        monkeypatch.chdir(tmp_path)

        assert (
            await run._run(
                str(tmp_path),
                session=None,
                llm=None,
                io_mode="cli",
                extra_creatures=[],
                extra_channels=[],
            )
            == 0
        )
        assert milestones == [
            ("engine_create_begin", {"surface": "cli"}),
            ("engine_entered", {"surface": "cli"}),
            (
                "creature_added",
                {"surface": "cli", "creature_id": "focus", "graph_id": "graph"},
            ),
            ("surface_run_begin", {"surface": "cli", "creature_id": "focus"}),
        ]


async def _async_none():
    return None


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
