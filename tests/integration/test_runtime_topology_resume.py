"""Runtime-topology persistence: does ``service.add_channel`` /
``service.connect`` *after* the initial recipe survive a resume?

Reproduces the user-asked question: "if I manually connect two
creatures at runtime, does the connection persist?"

Pre-fix expectation: NO. The recipe-described topology comes back,
but every runtime mutation is lost — they never reach the session
store.

After the fix lands: the runtime-added channel + wiring should
survive resume.
"""

import asyncio
from pathlib import Path

import pytest
import yaml

from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.core import agent_compact as _agent_compact
from kohakuterrarium.core import agent_model as _agent_model
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.graph_manifest import MANIFEST_KEY
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.llm import ScriptedLLM

pytestmark = pytest.mark.timeout(60)

SERVICE = ActorRef("service", "ops")


def _drive_kwargs() -> dict:
    return dict(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )


async def _wait_for(predicate, *, timeout: float = 8.0):
    """Poll ``predicate`` (async) until truthy or timeout — returns the value."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(0.05)
    return await predicate()


@pytest.fixture
def patched_llm(monkeypatch):
    def _fake_create(config, llm=None):
        return ScriptedLLM(["ack"])

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)


@pytest.fixture
def patched_llm_drive(monkeypatch):
    # Drive delivery drives several controller turns; give the fake plenty.
    def _fake_create(config, llm=None):
        return ScriptedLLM(["ack"] * 50)

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)


@pytest.fixture
def patched_llm_resume(monkeypatch):
    # Resume rebuilds the agent by profile name, so the profile-resolution
    # factories must be seamed too (not just create_llm_provider) — otherwise
    # a resumed creature's LLM build escapes to a real provider. Mirrors the
    # e2e ``install_scripted_llm`` five-site patch.
    def _fake_create(config, llm=None):
        return ScriptedLLM(["ack"] * 50)

    def _fake_from_profile(name):
        return ScriptedLLM(["ack"] * 50)

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)
    monkeypatch.setattr(
        _bootstrap_llm, "create_llm_from_profile_name", _fake_from_profile
    )
    monkeypatch.setattr(
        _agent_model, "create_llm_from_profile_name", _fake_from_profile
    )
    monkeypatch.setattr(
        _agent_compact, "create_llm_from_profile_name", _fake_from_profile
    )


async def _has_reason_delivery(manager, drive_id, reasons):
    for delivery in await manager.list_deliveries(drive_id):
        if delivery.reason in reasons:
            return delivery
    return None


async def _has_acked_delivery(manager, drive_id):
    for delivery in await manager.list_deliveries(drive_id):
        if delivery.state == "acknowledged":
            return delivery
    return None


def _write_creature_dir(root: Path, name: str) -> Path:
    cdir = root / f"creature_{name}"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "agent.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "system_prompt": f"You are {name}.",
                "llm_profile": "test/scripted",
                "model": "scripted-model",
                "provider": "test",
                "input": {"type": "none"},
                "output": {"type": "stdout"},
                "tool_format": "bracket",
                "include_tools_in_prompt": False,
                "include_hints_in_prompt": False,
            }
        ),
        encoding="utf-8",
    )
    return cdir


def _write_recipe(root: Path) -> Path:
    """Write a recipe with 2 creatures and NO channels in the recipe —
    the test adds the channel at runtime via ``service.add_channel``."""
    _write_creature_dir(root, "alice")
    _write_creature_dir(root, "bob")
    rdir = root / "duo"
    rdir.mkdir(parents=True, exist_ok=True)
    rpath = rdir / "terrarium.yaml"
    rpath.write_text(
        yaml.safe_dump(
            {
                "name": "duo",
                "creatures": [
                    {"name": "alice", "base_config": str(root / "creature_alice")},
                    {"name": "bob", "base_config": str(root / "creature_bob")},
                ],
                "channels": {},
            }
        ),
        encoding="utf-8",
    )
    return rpath


class TestRuntimeTopologyResume:
    """One workflow: apply recipe → runtime add_channel + connect →
    close → cold-reopen + adopt_session → verify channel + wiring."""

    async def test_runtime_added_channel_and_wiring_survive_resume(
        self, patched_llm, tmp_path
    ):
        recipe_path = _write_recipe(tmp_path)
        recipe = load_terrarium_config(str(recipe_path))
        sess_dir = tmp_path / "sessions"
        sess_dir.mkdir()

        # --- phase 1: apply recipe, runtime-mutate, save, shutdown -------
        from kohakuterrarium.session.store import SessionStore

        engine1 = Terrarium(pwd=str(tmp_path), session_dir=str(sess_dir))
        graph = await engine1.apply_recipe(recipe, pwd=str(tmp_path))
        gid = graph.graph_id

        store_path = sess_dir / f"{gid}.kohakutr"
        store = SessionStore(str(store_path))
        store.init_meta(
            session_id=gid,
            config_type="terrarium",
            config_path=str(recipe_path),
            pwd=str(tmp_path),
            agents=["alice", "bob"],
            terrarium_name="duo",
            terrarium_channels=[],
            terrarium_creatures=[
                {"name": "alice", "listen": [], "send": []},
                {"name": "bob", "listen": [], "send": []},
            ],
        )
        await engine1.attach_session(gid, store)

        service = LocalTerrariumService(engine1)

        # Runtime mutation — the thing the user is asking about.
        await service.add_channel(gid, "manual_chat", "user-added at runtime")
        await service.connect("alice", "bob", channel="manual_chat")

        # Pre-shutdown sanity: live engine has the wiring.
        info_alice = await service.get_creature_info("alice")
        info_bob = await service.get_creature_info("bob")
        assert "manual_chat" in info_alice.send_channels
        assert "manual_chat" in info_bob.listen_channels

        store.flush()
        await engine1.shutdown()
        store.close()

        # --- phase 2: cold engine, adopt the saved file -----------------
        engine2 = Terrarium(pwd=str(tmp_path), session_dir=str(sess_dir))
        # ``adopt_session`` may mint a fresh graph_id (apply_recipe
        # generates them); the saved-session identity check happens
        # via the saved meta's ``agents`` list rather than gid equality.
        sid = await engine2.adopt_session(store_path, pwd=str(tmp_path))

        service2 = LocalTerrariumService(engine2)
        chans = await service2.list_channels(sid)
        chan_names = {c.name for c in chans}
        info_alice2 = await service2.get_creature_info("alice")
        info_bob2 = await service2.get_creature_info("bob")

        await engine2.shutdown()

        # THE GAP: pre-fix, "manual_chat" disappears and alice / bob
        # come back without the wiring. These asserts pin the desired
        # post-fix behaviour.
        assert "manual_chat" in chan_names, (
            f"runtime-added channel did not survive resume; "
            f"channels after resume = {chan_names!r}"
        )
        assert "manual_chat" in info_alice2.send_channels, (
            f"alice wiring lost after resume; send_channels = "
            f"{info_alice2.send_channels!r}"
        )
        assert "manual_chat" in info_bob2.listen_channels, (
            f"bob wiring lost after resume; listen_channels = "
            f"{info_bob2.listen_channels!r}"
        )

        # --- phase 3: SECOND cold resume off the same store -------------
        # The first replay used to overwrite the saved snapshot mid-
        # restore (channels written while edges were still empty), so
        # the wiring survived cycle 1 but vanished on cycle 2.
        engine3 = Terrarium(pwd=str(tmp_path), session_dir=str(sess_dir))
        sid3 = await engine3.adopt_session(store_path, pwd=str(tmp_path))

        service3 = LocalTerrariumService(engine3)
        chans3 = {c.name for c in await service3.list_channels(sid3)}
        info_alice3 = await service3.get_creature_info("alice")
        info_bob3 = await service3.get_creature_info("bob")

        await engine3.shutdown()

        assert (
            "manual_chat" in chans3
        ), f"channel lost on SECOND resume; channels = {chans3!r}"
        assert "manual_chat" in info_alice3.send_channels, (
            f"alice wiring lost on SECOND resume; send_channels = "
            f"{info_alice3.send_channels!r}"
        )
        assert "manual_chat" in info_bob3.listen_channels, (
            f"bob wiring lost on SECOND resume; listen_channels = "
            f"{info_bob3.listen_channels!r}"
        )

    async def test_live_drive_redelivers_after_creature_restart(
        self, patched_llm_drive, tmp_path
    ):
        """A live Drive assigned to a real creature redelivers after that
        creature stops and restarts (§6.1 reconcile), while a runtime-added
        channel (a topology leftover) keeps working.

        Ephemeral drives keep this fast + deterministic: session-backed drives
        share the ``.kohakutr`` file with KVault, so a live dispatcher serializes
        against conversation writes (Phase 0 busy_timeout tradeoff); the durable
        path is pinned by the unit persistent-merge + resume-reconcile tests."""
        recipe_path = _write_recipe(tmp_path)

        engine = Terrarium(pwd=str(tmp_path), **_drive_kwargs())
        async with engine:
            graph = await engine.apply_recipe(str(recipe_path), pwd=str(tmp_path))
            gid = graph.graph_id
            assert engine.drives.durability == "ephemeral"

            service = LocalTerrariumService(engine)
            await service.add_channel(gid, "manual_chat", "runtime")
            await service.connect("alice", "bob", channel="manual_chat")

            alice = next(c for c in engine.list_creatures() if c.name == "alice")
            manager = engine.drives.manager_for(gid)
            rec = await manager.create_drive(
                CreateDriveRequest(
                    kind="generic",
                    title="watch deploy",
                    scope_type="graph",
                    scope_id=gid,
                    owner=SERVICE,
                    owner_scope="service",
                    created_by=SERVICE,
                    assignee_creature_id=alice.creature_id,
                    spec={"instruction": "monitor"},
                ),
                actor=SERVICE,
                graph_id=gid,
                is_privileged=True,
            )
            did = rec.drive_id
            # The activated delivery reaches alice and settles (acknowledged).
            assert await _wait_for(
                lambda: _has_acked_delivery(manager, did), timeout=25.0
            ), "activated Drive delivery never settled"

            # Restart alice (stable creature id): reconcile redelivers (§6.1) —
            # the prior delivery is settled, so it is not counted as still-live.
            await engine.stop(alice)
            await engine.start(alice)
            assert await _wait_for(
                lambda: _has_reason_delivery(manager, did, {"resume", "recovery"}),
                timeout=25.0,
            ), "Drive did not redeliver after creature restart"

            # The Drive record is intact and the runtime channel (a leftover)
            # still routes.
            still = await manager.get_drive(did)
            assert still is not None and still.title == "watch deploy"
            chans = {c.name for c in await service.list_channels(gid)}
            assert "manual_chat" in chans

    async def test_resume_drive_enabled_session_completes(
        self, patched_llm_resume, tmp_path
    ):
        """Resuming a session on a Drive-ENABLED engine with a session-backed
        (persistent) Drive must COMPLETE, not hang (the 32d/32g journey repro).

        The persisted Drive's repository shares the ``.kohakutr`` with KVault, so
        a dispatcher/reconcile started during ``adopt_session`` must not stall the
        resume against the store writes. ``adopt_session`` is bounded here; before
        the fix it never returns."""
        recipe = _write_recipe(tmp_path)
        sess = tmp_path / "sessions"

        engine1 = Terrarium(pwd=str(tmp_path), session_dir=str(sess), **_drive_kwargs())
        async with engine1:
            graph = await engine1.apply_recipe(str(recipe), pwd=str(tmp_path))
            gid = graph.graph_id
            # session_dir -> the Drive repo is session-backed (persistent).
            assert engine1.drives.durability == "persistent"
            alice = next(c for c in engine1.list_creatures() if c.name == "alice")
            manager = engine1.drives.manager_for(gid)
            rec = await manager.create_drive(
                CreateDriveRequest(
                    kind="generic",
                    title="watch deploy",
                    scope_type="graph",
                    scope_id=gid,
                    owner=SERVICE,
                    owner_scope="service",
                    created_by=SERVICE,
                    assignee_creature_id=alice.creature_id,
                    spec={"instruction": "monitor"},
                ),
                actor=SERVICE,
                graph_id=gid,
                is_privileged=True,
            )
            did = rec.drive_id
            store_path = engine1._session_stores[gid].path

        # Resume into a fresh Drive-enabled engine — MUST complete within bound.
        engine2 = Terrarium(
            pwd=str(tmp_path), session_dir=str(tmp_path / "resumed"), **_drive_kwargs()
        )
        try:
            sid = await asyncio.wait_for(
                engine2.adopt_session(store_path, pwd=str(tmp_path)), timeout=25.0
            )
            assert sid, "adopt_session returned no graph id"
            # The persisted Drive came back on the resumed engine.
            rmgr = engine2.drives.manager_for(sid)
            assert await rmgr.get_drive(did) is not None
        finally:
            await engine2.shutdown()

    async def test_agent_resume_remaps_assigned_drive_to_reminted_creature(
        self, patched_llm_resume, tmp_path
    ):
        """R1-43 across both resume paths. A manifest resume restores the
        creature's exact runtime id, so the persisted assignment stays valid
        as-is. A legacy (manifest-less) resume re-mints the id
        (``_safe_creature_id``), so an assignment naming the OLD id must be
        remapped to the resumed creature — else reconcile runs for the new id
        and the saved assignment never redelivers (a silent failure)."""
        _write_creature_dir(tmp_path, "solo")
        agent_yaml = str(tmp_path / "creature_solo" / "agent.yaml")
        store_path = str(tmp_path / "solo.kohakutr")

        engine1 = Terrarium(pwd=str(tmp_path), **_drive_kwargs())
        async with engine1:
            solo = await engine1.add_creature(
                agent_yaml, session=store_path, start=True
            )
            gid = solo.graph_id
            old_id = solo.creature_id
            assert old_id != "solo"  # standalone add re-mints <name>_<random>
            rec = await engine1.drives.manager_for(gid).create_drive(
                CreateDriveRequest(
                    kind="generic",
                    title="watch",
                    scope_type="graph",
                    scope_id=gid,
                    owner=SERVICE,
                    owner_scope="service",
                    created_by=SERVICE,
                    assignee_creature_id=old_id,
                    spec={"instruction": "monitor"},
                ),
                actor=SERVICE,
                graph_id=gid,
                is_privileged=True,
            )
            did = rec.drive_id

        # Shutdown checkpointed a manifest — resume restores the exact
        # creature id, so the saved assignment needs no remap at all.
        engine2 = Terrarium(
            pwd=str(tmp_path), session_dir=str(tmp_path / "resumed"), **_drive_kwargs()
        )
        try:
            sid = await asyncio.wait_for(
                engine2.adopt_session(store_path, pwd=str(tmp_path)), timeout=25.0
            )
            rmgr = engine2.drives.manager_for(sid)
            resumed = engine2.list_creatures()[0]
            assert resumed.creature_id == old_id

            async def _still_assigned():
                a = await rmgr.get_assignment(did)
                if a is not None and a.assignee_creature_id == old_id:
                    return a
                return None

            assert await _wait_for(
                _still_assigned, timeout=25.0
            ), "manifest resume must keep the assignment on the restored id"
        finally:
            await engine2.shutdown()

        # Tombstone the manifest (the checkpoint's invalidation marker) to
        # force the legacy agent path: id re-mints, assignment must remap.
        tomb = SessionStore(store_path)
        tomb.meta[MANIFEST_KEY] = None
        tomb.flush()
        tomb.close(update_status=False)

        engine3 = Terrarium(
            pwd=str(tmp_path), session_dir=str(tmp_path / "resumed3"), **_drive_kwargs()
        )
        try:
            sid = await asyncio.wait_for(
                engine3.adopt_session(store_path, pwd=str(tmp_path)), timeout=25.0
            )
            rmgr = engine3.drives.manager_for(sid)
            resumed = engine3.list_creatures()[0]
            assert resumed.creature_id != old_id  # id was re-minted on resume

            async def _remapped():
                a = await rmgr.get_assignment(did)
                if a is not None and a.assignee_creature_id == resumed.creature_id:
                    return a
                return None

            assert await _wait_for(
                _remapped, timeout=25.0
            ), "assignment was not remapped to the resumed creature (R1-43)"
        finally:
            await engine3.shutdown()

    async def test_cross_graph_wire_drains_drive_rows_immediately(
        self, patched_llm, tmp_path
    ):
        """R1-12: a cross-graph merge through ``ensure_same_graph`` (the
        ``group_wire`` path) must drain pending Drive row movement BEFORE
        returning, so the survivor lists both graphs' Drives immediately —
        not only after some later unrelated topology operation."""
        import kohakuterrarium.terrarium.channels as channels

        _write_creature_dir(tmp_path, "alice")
        _write_creature_dir(tmp_path, "bob")
        engine = Terrarium(pwd=str(tmp_path), **_drive_kwargs())

        def _graph_drive(gid, assignee):
            return CreateDriveRequest(
                kind="generic",
                title=f"watch {gid}",
                scope_type="graph",
                scope_id=gid,
                owner=SERVICE,
                owner_scope="service",
                created_by=SERVICE,
                assignee_creature_id=assignee,
                spec={"instruction": "monitor"},
            )

        async with engine:
            alice = await engine.add_creature(
                str(tmp_path / "creature_alice" / "agent.yaml"), start=False
            )
            bob = await engine.add_creature(
                str(tmp_path / "creature_bob" / "agent.yaml"), start=False
            )
            assert alice.graph_id != bob.graph_id  # two disconnected graphs
            a_gid, b_gid = alice.graph_id, bob.graph_id
            rec_a = await engine.drives.manager_for(a_gid).create_drive(
                _graph_drive(a_gid, alice.creature_id),
                actor=SERVICE,
                graph_id=a_gid,
                is_privileged=True,
            )
            rec_b = await engine.drives.manager_for(b_gid).create_drive(
                _graph_drive(b_gid, bob.creature_id),
                actor=SERVICE,
                graph_id=b_gid,
                is_privileged=True,
            )

            keep_gid = await channels.ensure_same_graph(engine, alice, bob)

            # Drained inside ensure_same_graph: the survivor lists BOTH Drives.
            survivor = engine.drives.manager_for(keep_gid)
            assert await survivor.get_drive(rec_a.drive_id) is not None
            assert await survivor.get_drive(rec_b.drive_id) is not None
            # The absorbed graph's manager is gone (one canonical repo/Drive).
            dropped = b_gid if keep_gid != b_gid else a_gid
            assert engine.drives.peek_manager(dropped) is None
