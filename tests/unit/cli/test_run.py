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
        from kohakuterrarium.session import store as store_module

        store = _FailingPreviewStore()
        monkeypatch.setattr(
            store_module.SessionStore, "open_readonly", lambda _path: store
        )

        assert run._session_preview(tmp_path / "broken.kohakutr") == ""
        assert store.closed is True


class TestCliTopology:
    @pytest.mark.asyncio
    async def test_channel_updates_creature_channel_lists(self, monkeypatch):
        creature = type(
            "Creature",
            (),
            {
                "name": "focus",
                "creature_id": "c1",
                "agent": object(),
                "listen_channels": [],
                "send_channels": [],
            },
        )()
        graph = type("Graph", (), {"creature_ids": {"c1"}})()
        registry = object()
        environment = type("Environment", (), {"shared_channels": registry})()
        engine = type(
            "Engine",
            (),
            {
                "_topology": object(),
                "_environments": {"g1": environment},
                "add_channel": lambda self, *_args: _async_none(),
                "get_graph": lambda self, _graph_id: graph,
                "get_creature": lambda self, _creature_id: creature,
            },
        )()

        monkeypatch.setattr(
            "kohakuterrarium.terrarium.channels.inject_channel_trigger",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            "kohakuterrarium.terrarium.topology.set_listen",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            "kohakuterrarium.terrarium.topology.set_send",
            lambda *_args, **_kwargs: None,
        )

        await run._apply_cli_topology(
            engine,
            graph_id="g1",
            pwd=".",
            llm=None,
            extra_creatures=[],
            extra_channels=["reviews"],
        )

        assert creature.listen_channels == ["reviews"]
        assert creature.send_channels == ["reviews"]


class TestRunStartupTrace:
    @pytest.mark.asyncio
    async def test_records_engine_and_surface_milestones(self, monkeypatch, tmp_path):
        milestones = []
        registrations = []
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

        from kohakuterrarium.studio.identity import drive_settings
        from kohakuterrarium.terrarium import engine as engine_module

        monkeypatch.setattr(drive_settings, "resolve_drive_kwargs", lambda: {})
        monkeypatch.setattr(
            run,
            "register_group_hooks",
            lambda: registrations.append("registered"),
            raising=False,
        )
        monkeypatch.setattr(engine_module, "Terrarium", lambda **_kwargs: _Engine())
        monkeypatch.setattr(run, "_looks_like_recipe", lambda _path: False)
        monkeypatch.setattr(
            run, "_attach_session_store", lambda *_args, **_kwargs: None
        )
        monkeypatch.setattr(
            run, "_apply_cli_topology", lambda *_args, **_kwargs: _async_none()
        )
        from kohakuterrarium.terrarium import engine_rich_cli

        monkeypatch.setattr(engine_rich_cli, "run_engine_with_rich_cli", _run_cli)
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
        assert registrations == ["registered"]
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
