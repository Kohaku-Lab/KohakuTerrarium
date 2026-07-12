"""Unit tests for :mod:`kohakuterrarium.cli.drive` (``kt drive``).

The heavy ``Studio.resume`` is stubbed to hand back a real Drive-enabled
``LocalTerrariumService`` so the subcommand dispatch, ``--json`` vs table
rendering, and the meaningful exit codes are exercised without a full resume.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.cli import drive as drive_cli_mod
from kohakuterrarium.studio.sessions import drives as facade
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)

OPS = ActorRef("user", "local")


async def _service_with_drive():
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    await engine.__aenter__()
    root = Creature(
        creature_id="root",
        name="root",
        agent=_FakeAgent(name="root"),
        is_privileged=True,
    )
    await engine.add_creature(root)
    gid = root.graph_id
    service = LocalTerrariumService(engine)
    created = await facade.create_record(
        service,
        graph_id=gid,
        actor=OPS,
        body={"kind": "generic", "title": "watch"},
        is_privileged=True,
        operator=True,
    )
    return service, engine, created["drive_id"], created["revision"]


async def _service_two_graphs():
    """Two disconnected singleton graphs (each a privileged root creature)."""
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    await engine.__aenter__()
    root_a = Creature(
        creature_id="root-a",
        name="root-a",
        agent=_FakeAgent(name="root-a"),
        is_privileged=True,
    )
    await engine.add_creature(root_a)
    root_b = Creature(
        creature_id="root-b",
        name="root-b",
        agent=_FakeAgent(name="root-b"),
        is_privileged=True,
    )
    await engine.add_creature(root_b)  # fresh singleton graph — disconnected
    return LocalTerrariumService(engine), engine, root_a.graph_id, root_b.graph_id


def _patch_resume(monkeypatch, service, engine):
    async def _shutdown():
        await engine.shutdown()

    fake_studio = SimpleNamespace(service=service, shutdown=_shutdown)

    async def fake_resume(path, **_kw):
        return fake_studio

    monkeypatch.setattr(
        drive_cli_mod, "_resolve_session", lambda q: Path("session.kohakutr")
    )
    monkeypatch.setattr(
        "kohakuterrarium.studio.studio.Studio.resume", staticmethod(fake_resume)
    )


def _args(**kw):
    base = dict(
        session="s",
        json=False,
        status=None,
        kind=None,
        mine=False,
        reason=None,
        graph=None,
        creature=None,
        priority=0,
        spec_json=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestReads:
    async def test_list_json(self, monkeypatch, capsys):
        svc, engine, did, _ = await _service_with_drive()
        _patch_resume(monkeypatch, svc, engine)
        code = await drive_cli_mod._run(_args(json=True, drive_command="list"), "list")
        out = capsys.readouterr().out
        assert code == drive_cli_mod.EXIT_OK
        assert '"drives"' in out and did in out

    async def test_list_table(self, monkeypatch, capsys):
        svc, engine, did, _ = await _service_with_drive()
        _patch_resume(monkeypatch, svc, engine)
        code = await drive_cli_mod._run(_args(drive_command="list"), "list")
        out = capsys.readouterr().out
        assert code == drive_cli_mod.EXIT_OK
        assert "STATUS" in out and did in out

    async def test_show_not_found(self, monkeypatch, capsys):
        svc, engine, _, _ = await _service_with_drive()
        _patch_resume(monkeypatch, svc, engine)
        code = await drive_cli_mod._run(
            _args(drive_command="show", drive_id="ghost"), "show"
        )
        assert code == drive_cli_mod.EXIT_NOT_FOUND


class TestExitCodes:
    async def test_transition_conflict_exit_code(self, monkeypatch, capsys):
        svc, engine, did, _rev = await _service_with_drive()
        _patch_resume(monkeypatch, svc, engine)
        code = await drive_cli_mod._run(
            _args(
                drive_command="transition", drive_id=did, status="paused", revision=999
            ),
            "transition",
        )
        assert code == drive_cli_mod.EXIT_CONFLICT

    async def test_transition_missing_revision_usage(self, monkeypatch, capsys):
        svc, engine, did, _ = await _service_with_drive()
        _patch_resume(monkeypatch, svc, engine)
        code = await drive_cli_mod._run(
            _args(
                drive_command="transition", drive_id=did, status="paused", revision=None
            ),
            "transition",
        )
        out = capsys.readouterr().out
        assert code == drive_cli_mod.EXIT_USAGE
        assert "--revision" in out

    async def test_transition_success(self, monkeypatch, capsys):
        svc, engine, did, rev = await _service_with_drive()
        _patch_resume(monkeypatch, svc, engine)
        code = await drive_cli_mod._run(
            _args(
                drive_command="transition", drive_id=did, status="paused", revision=rev
            ),
            "transition",
        )
        out = capsys.readouterr().out
        assert code == drive_cli_mod.EXIT_OK
        assert "paused" in out


class TestMultiGraphScoping:
    """R1-33: graph-scoped ops must select an explicit graph, never creatures[0]."""

    async def test_create_ambiguous_graph_rejected(self, monkeypatch, capsys):
        svc, engine, _ga, _gb = await _service_two_graphs()
        _patch_resume(monkeypatch, svc, engine)
        code = await drive_cli_mod._run(
            _args(drive_command="create", kind="generic", title="t", scope="graph"),
            "create",
        )
        out = capsys.readouterr().out
        assert code != drive_cli_mod.EXIT_OK
        assert "--graph" in out

    async def test_create_targets_selected_graph(self, monkeypatch, capsys):
        svc, engine, _ga, gb = await _service_two_graphs()
        _patch_resume(monkeypatch, svc, engine)
        code = await drive_cli_mod._run(
            _args(
                drive_command="create",
                kind="generic",
                title="in-b",
                scope="graph",
                graph=gb,
                json=True,
            ),
            "create",
        )
        out = capsys.readouterr().out
        assert code == drive_cli_mod.EXIT_OK
        data = json.loads(out)
        assert data["scope_type"] == "graph"
        assert data["scope_id"] == gb

    async def test_create_unknown_graph_rejected(self, monkeypatch, capsys):
        svc, engine, _ga, _gb = await _service_two_graphs()
        _patch_resume(monkeypatch, svc, engine)
        code = await drive_cli_mod._run(
            _args(
                drive_command="create",
                kind="generic",
                title="t",
                scope="graph",
                graph="ghost-graph",
            ),
            "create",
        )
        out = capsys.readouterr().out
        assert code != drive_cli_mod.EXIT_OK
        assert "ghost-graph" in out

    async def test_assign_uses_assignee_creature_graph(self, monkeypatch, capsys):
        # ``root-b`` lives in graph gb, but is NOT creatures[0] (that is root-a
        # in ga); the assignee graph must be resolved from the creature itself.
        svc, engine, _ga, gb = await _service_two_graphs()
        _patch_resume(monkeypatch, svc, engine)
        captured: dict[str, object] = {}

        async def _capture(
            service, drive_id, *, assignee_creature_id, assignee_graph_id, **kw
        ):
            captured["graph"] = assignee_graph_id
            captured["creature"] = assignee_creature_id
            return {"drive_id": drive_id, "assignee_creature_id": assignee_creature_id}

        monkeypatch.setattr(drive_cli_mod._drives, "assign_record", _capture)
        code = await drive_cli_mod._run(
            _args(
                drive_command="assign", drive_id="d-x", creature="root-b", revision=1
            ),
            "assign",
        )
        assert code == drive_cli_mod.EXIT_OK
        assert captured["graph"] == gb
        assert captured["creature"] == "root-b"


class TestCreatureScope:
    """R1-33: creature scope must carry an explicit creature, not the graph."""

    async def test_creature_scope_requires_creature(self, monkeypatch, capsys):
        svc, engine, _did, _rev = await _service_with_drive()
        _patch_resume(monkeypatch, svc, engine)
        code = await drive_cli_mod._run(
            _args(drive_command="create", kind="generic", title="t", scope="creature"),
            "create",
        )
        out = capsys.readouterr().out
        assert code == drive_cli_mod.EXIT_USAGE
        assert "--creature" in out

    async def test_creature_scope_uses_creature_id(self, monkeypatch, capsys):
        svc, engine, _did, _rev = await _service_with_drive()
        _patch_resume(monkeypatch, svc, engine)
        code = await drive_cli_mod._run(
            _args(
                drive_command="create",
                kind="generic",
                title="t",
                scope="creature",
                creature="root",
                json=True,
            ),
            "create",
        )
        out = capsys.readouterr().out
        assert code == drive_cli_mod.EXIT_OK
        data = json.loads(out)
        assert data["scope_type"] == "creature"
        assert data["scope_id"] == "root"


class TestGlobalReadsNeedNoGraph:
    """R1-33: id-addressed reads must not require graph/creature resolution."""

    async def test_show_does_not_resolve_topology(self, monkeypatch, capsys):
        svc, engine, did, _rev = await _service_with_drive()
        _patch_resume(monkeypatch, svc, engine)

        async def _boom(*_a, **_k):
            raise AssertionError("id-addressed show must not resolve a graph")

        monkeypatch.setattr(svc, "list_creatures", _boom)
        monkeypatch.setattr(svc, "list_graphs", _boom)
        code = await drive_cli_mod._run(
            _args(drive_command="show", drive_id=did, json=True), "show"
        )
        out = capsys.readouterr().out
        assert code == drive_cli_mod.EXIT_OK
        assert did in out

    async def test_deliveries_does_not_resolve_topology(self, monkeypatch, capsys):
        svc, engine, did, _rev = await _service_with_drive()
        _patch_resume(monkeypatch, svc, engine)

        async def _boom(*_a, **_k):
            raise AssertionError("id-addressed deliveries must not resolve a graph")

        monkeypatch.setattr(svc, "list_creatures", _boom)
        monkeypatch.setattr(svc, "list_graphs", _boom)
        code = await drive_cli_mod._run(
            _args(drive_command="deliveries", drive_id=did, json=True), "deliveries"
        )
        assert code == drive_cli_mod.EXIT_OK


class TestDispatch:
    def test_no_subcommand_usage(self, capsys):
        code = drive_cli_mod.drive_cli(SimpleNamespace(drive_command=None))
        assert code == drive_cli_mod.EXIT_USAGE
