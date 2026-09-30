"""Unit tests for :mod:`kohakuterrarium.terrarium.autosession` (E2).

Pins the engine-owned persistence contract that replaces the manual
``SessionStore`` + ``init_meta`` + ``attach_session`` ceremony — and
the failure modes that ceremony used to invite (free-string
``config_type`` corrupting resumability, files stuck ``running``).
"""

import asyncio

import pytest

from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.config_serde import pack_agent_config
from kohakuterrarium.session.resume import detect_session_type
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium import autosession, graph_checkpoint
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.testing.terrarium import _FakeAgent


def _prebuilt(name="alice"):
    return Creature(
        creature_id=name,
        name=name,
        agent=_FakeAgent(name=name),
        config_snapshot=pack_agent_config(AgentConfig(name=name)),
        build_pwd=".",
    )


def _write_cfg(tmp_path, name="auto"):
    d = tmp_path / "creature"
    d.mkdir(exist_ok=True)
    (d / "config.yaml").write_text(
        f"name: {name}\ninput:\n  type: none\noutput:\n  type: none\n",
        encoding="utf-8",
    )
    return d


class TestAutosessionViaSessionDir:
    async def test_session_dir_mints_store_per_graph(self, tmp_path):
        # The headline E2 fix: ``Terrarium(session_dir=...)`` actually
        # persists — it used to be consumed only by graph split/merge.
        sess_dir = tmp_path / "runs"
        t = Terrarium(session_dir=str(sess_dir))
        try:
            c = await t.add_creature(_prebuilt("alice"), start=False)
            store_path = sess_dir / f"{c.creature_id}.kohakutr"
            assert store_path.exists()
            store = t._session_stores[c.graph_id]
            meta = store.load_meta()
            assert meta["config_type"] == "agent"
            assert meta["agents"] == ["alice"]
            assert meta["session_id"] == c.creature_id
            assert meta["status"] == "running"
            manifest = meta["live_graph_manifest"]
            assert manifest["graph_id"] == c.graph_id
            assert manifest["revision"] == 2
            assert [item["name"] for item in manifest["creatures"]] == ["alice"]
            await t.add_channel(c.graph_id, "tasks", description="Work")
            assert store.meta["live_graph_manifest"]["revision"] == 3
            assert store.meta["live_graph_manifest"]["channels"] == [
                {"name": "tasks", "description": "Work"}
            ]
            await t.remove_creature(c)
            assert store.meta["live_graph_manifest"] is None
        finally:
            await t.shutdown()
        # shutdown closed the minted store — no more stuck "running".
        reopened = SessionStore(store_path)
        try:
            assert reopened.load_meta()["status"] == "paused"
        finally:
            reopened.close(update_status=False)

    async def test_file_uri_session_dir_mints_into_the_named_directory(
        self, tmp_path, monkeypatch
    ):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        sess_dir = tmp_path / "runs"
        t = Terrarium(session_dir=sess_dir.resolve().as_uri())
        try:
            c = await t.add_creature(_prebuilt("alice"), start=False)
            assert (sess_dir / f"{c.creature_id}.kohakutr").exists()
            assert not (cwd / "file:").exists()
        finally:
            await t.shutdown()

    async def test_shutdown_closes_owned_store_when_stop_cancelled(self, tmp_path):
        # The stop loop can be cancelled mid-await; store closure runs in
        # a ``finally`` so a leaked writer lock (which blocks any later
        # adopt of the same file) can't outlive shutdown.
        t = Terrarium(session_dir=str(tmp_path / "runs"))
        c = await t.add_creature(_prebuilt("alice"), start=False)
        store = t._session_stores[c.graph_id]
        assert c.graph_id in t._owned_sessions
        # Force the shutdown loop to enter the stop branch, then have the
        # stop itself get cancelled. is_running now derives from status, so
        # the creature must look started (idle) — set _ever_started too.
        c._running = True
        c.agent._running = True
        c._ever_started = True
        t._running = True

        async def _cancelled_stop(*_args, **_kwargs):
            raise asyncio.CancelledError()

        c.stop = _cancelled_stop
        with pytest.raises(asyncio.CancelledError):
            await t.shutdown()
        # Cancellation propagated, but the owned store was still closed
        # and the engine still marked itself stopped.
        assert getattr(store, "_closed", False) is True
        assert t._running is False

    async def test_session_false_disables_autosession(self, tmp_path):
        t = Terrarium(session_dir=str(tmp_path / "runs"))
        try:
            c = await t.add_creature(_prebuilt(), start=False, session=False)
            assert c.graph_id not in t._session_stores
            assert not (tmp_path / "runs").exists()
        finally:
            await t.shutdown()

    async def test_no_session_dir_no_autosession(self, tmp_path):
        t = Terrarium()
        try:
            c = await t.add_creature(_prebuilt(), start=False)
            assert c.graph_id not in t._session_stores
        finally:
            await t.shutdown()


class TestSessionArg:
    async def test_explicit_path_mints_exactly_there(self, tmp_path):
        target = tmp_path / "sub" / "student-42.kohakutr"
        t = Terrarium()
        try:
            c = await t.add_creature(
                _prebuilt("grader"), start=False, session=str(target)
            )
            assert target.exists()
            meta = t._session_stores[c.graph_id].load_meta()
            assert meta["agents"] == ["grader"]
        finally:
            await t.shutdown()
        # Closed on shutdown.
        reopened = SessionStore(target)
        try:
            assert reopened.load_meta()["status"] == "paused"
        finally:
            reopened.close(update_status=False)

    async def test_explicit_file_uri_mints_the_named_store(self, tmp_path, monkeypatch):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        target = tmp_path / "sub" / "student-42.kohakutr"
        t = Terrarium()
        try:
            await t.add_creature(
                _prebuilt("grader"), start=False, session=target.resolve().as_uri()
            )
            assert target.exists()
            assert not (cwd / "file:").exists()
        finally:
            await t.shutdown()

    async def test_true_uses_default_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "envdir"))
        t = Terrarium()
        try:
            c = await t.add_creature(_prebuilt(), start=False, session=True)
            assert (tmp_path / "envdir" / f"{c.creature_id}.kohakutr").exists()
        finally:
            await t.shutdown()

    async def test_custom_store_attached_as_is(self, tmp_path):
        path = tmp_path / "own.kohakutr"
        store = SessionStore(path)
        store.init_meta(
            session_id="custom",
            config_type="agent",
            config_path="",
            pwd=".",
            agents=[],
        )
        t = Terrarium()
        try:
            c = await t.add_creature(_prebuilt("bob"), start=False, session=store)
            assert t._session_stores[c.graph_id] is store
            # The creature folded into the caller's meta.
            assert "bob" in store.meta["agents"]
            # Caller-owned store: NOT closed by shutdown.
            assert c.graph_id not in t._owned_sessions
        finally:
            await t.shutdown()
            store.close(update_status=False)

    async def test_invalid_session_type_raises(self, tmp_path):
        t = Terrarium()
        try:
            with pytest.raises(TypeError, match="session= accepts"):
                await t.add_creature(_prebuilt(), start=False, session=123)
        finally:
            await t.shutdown()

    async def test_joining_graph_with_store_folds_in(self, tmp_path):
        t = Terrarium(session_dir=str(tmp_path / "runs"))
        try:
            first = await t.add_creature(_prebuilt("alice"), start=False)
            store = t._session_stores[first.graph_id]
            await t.add_creature(_prebuilt("bob"), graph=first.graph_id, start=False)
            meta = store.load_meta()
            assert set(meta["agents"]) == {"alice", "bob"}
            # Two agents on one store → promoted for terrarium resume.
            assert meta["config_type"] == "terrarium"
        finally:
            await t.shutdown()

    async def test_joining_graph_with_closed_store_rebuilds(self, tmp_path):
        # Defensive: a closed store in engine._session_stores (e.g. after a
        # merge replaced it) must not be attached; a fresh store is minted.
        t = Terrarium(session_dir=str(tmp_path / "runs"))
        try:
            first = await t.add_creature(_prebuilt("alice"), start=False)
            stale = t._session_stores[first.graph_id]
            stale.close(update_status=False)
            # New creature on the same graph — previously would attach the
            # closed store and lose its session.
            await t.add_creature(_prebuilt("bob"), graph=first.graph_id, start=False)
            new_store = t._session_stores[first.graph_id]
            assert not getattr(new_store, "_closed", False)
            # The fresh store serves the joining creature; alice's history
            # lived in the closed store and is unrecoverable by design.
            meta = new_store.load_meta()
            assert "bob" in set(meta["agents"])
        finally:
            await t.shutdown()


class TestAttachSessionMintMode:
    async def test_path_attach_mints_with_meta(self, tmp_path):
        target = tmp_path / "mint.kohakutr"
        t = Terrarium()
        try:
            c = await t.add_creature(_prebuilt("solo"), start=False)
            await t.attach_session(c.graph_id, str(target))
            meta = t._session_stores[c.graph_id].load_meta()
            assert meta["session_id"] == c.graph_id
            assert meta["agents"] == ["solo"]
        finally:
            await t.shutdown()


class TestInitMetaValidation:
    def test_free_string_config_type_rejected(self, tmp_path):
        # The HW4 corruption: ``config_type="creature"`` silently made
        # 61 session files unresumable.  Now it fails AT WRITE TIME.
        store = SessionStore(tmp_path / "bad.kohakutr")
        try:
            with pytest.raises(ValueError, match="config_type must be"):
                store.init_meta(
                    session_id="x",
                    config_type="creature",
                    config_path="",
                    pwd=".",
                    agents=[],
                )
        finally:
            store.close(update_status=False)


class TestRealAgentRoundTrip:
    async def test_chat_persists_and_is_resumable(self, tmp_path, monkeypatch):
        # The 3-line replacement for the HW4 ceremony: path in,
        # resumable file out.
        cfg_dir = _write_cfg(tmp_path)
        target = tmp_path / "run.kohakutr"
        provider = ScriptedLLM(["The graded reply."])
        monkeypatch.setattr(
            "kohakuterrarium.bootstrap.agent_init.create_llm_provider",
            lambda *_args, **_kwargs: provider,
        )
        monkeypatch.setattr(
            "kohakuterrarium.bootstrap.llm.create_llm_provider",
            lambda *_args, **_kwargs: provider,
        )
        t = Terrarium(pwd=str(tmp_path))
        try:
            c = await t.add_creature(
                str(cfg_dir),
                llm="default",
                io="headless",
                session=str(target),
            )
            chunks = []
            async for chunk in c.chat("grade this"):
                chunks.append(chunk)
            assert "The graded reply." in "".join(chunks)
        finally:
            await t.shutdown()

        assert target.exists()
        # The file is RESUMABLE: typed correctly + conversation captured.
        assert detect_session_type(target) == "agent"
        reopened = SessionStore(target)
        try:
            meta = reopened.load_meta()
            assert meta["config_path"] == str(cfg_dir)
            assert meta["status"] == "paused"
            events = reopened.get_events("auto")
            assert any(e.get("type") == "user_input" for e in events)
        finally:
            reopened.close(update_status=False)


class TestSessionReplacement:
    async def test_failed_replacement_keeps_old_writer_live(self, tmp_path):
        t = Terrarium(
            pwd=str(tmp_path),
            drive_config=DriveRuntimeConfig(enabled=True),
            drive_registrations=default_registrations(),
        )
        cfg = AgentConfig(name="alice", system_prompt="Reply briefly.")
        c = await t.add_creature(
            cfg,
            llm=ScriptedLLM(["still working"]),
            io="headless",
            session=tmp_path / "old.kohakutr",
            start=False,
        )
        old = t._session_stores[c.graph_id]
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("block", encoding="utf-8")
        target = blocker / "new.kohakutr"
        try:
            with pytest.raises(OSError):
                await t.attach_session(c.graph_id, target)
            assert t._session_stores[c.graph_id] is old
            assert not old._closed
            assert c.agent.session_store is old
            assert c.graph_id in t._owned_sessions
            old.append_event("alice", "user_input", {"content": "after failure"})
            old.flush()
            assert old.get_events("alice")[-1]["content"] == "after failure"
        finally:
            await t.shutdown()

    @pytest.mark.parametrize("reattach", [False, True])
    async def test_same_path_attach_and_join_reuse_writer(self, tmp_path, reattach):
        t = Terrarium(pwd=str(tmp_path))
        target = tmp_path / "shared.kohakutr"
        c = await t.add_creature(
            AgentConfig(name="alice"),
            llm=ScriptedLLM(["a"]),
            io="headless",
            session=target,
            start=False,
        )
        old = t._session_stores[c.graph_id]
        try:
            if reattach:
                await t.attach_session(c.graph_id, target.resolve().as_uri())
            assert t._session_stores[c.graph_id] is old
            sibling = await t.add_creature(
                AgentConfig(name="bob"),
                llm=ScriptedLLM(["b"]),
                io="headless",
                graph=c.graph_id,
                session=str(target),
                start=False,
            )
            assert sibling.agent.session_store is old
            assert c.agent.session_store is old
            assert set(old.load_meta()["agents"]) == {"alice", "bob"}
            assert c.graph_id in t._owned_sessions
        finally:
            await t.shutdown()

    async def test_replacement_by_new_creature_preserves_ownership(self, tmp_path):
        t = Terrarium(pwd=str(tmp_path))
        c = await t.add_creature(
            AgentConfig(name="alice"),
            llm=ScriptedLLM(["a"]),
            io="headless",
            session=tmp_path / "first.kohakutr",
            start=False,
        )
        old = t._session_stores[c.graph_id]
        try:
            sibling = await t.add_creature(
                AgentConfig(name="bob"),
                llm=ScriptedLLM(["b"]),
                io="headless",
                graph=c.graph_id,
                session=tmp_path / "second.kohakutr",
                start=False,
            )
            new = t._session_stores[c.graph_id]
            assert new is not old and old._closed
            assert c.agent.session_store is sibling.agent.session_store is new
            assert c.graph_id in t._owned_sessions
        finally:
            await t.shutdown()

    def test_mint_metadata_failure_releases_writer(self, tmp_path):
        t = Terrarium()
        target = tmp_path / "invalid.kohakutr"
        with pytest.raises(ValueError, match="config_type"):
            autosession.mint_store(t, "graph", path=target, config_type="invalid")
        reopened = SessionStore(target, writer_lock=True)
        reopened.close(update_status=False)

    @pytest.mark.parametrize("minted", [True, False])
    async def test_cancelled_checkpoint_restores_writer_and_bindings(
        self, tmp_path, minted
    ):
        t = Terrarium(
            pwd=str(tmp_path),
            drive_config=DriveRuntimeConfig(enabled=True),
            drive_registrations=default_registrations(),
        )
        c = await t.add_creature(
            AgentConfig(name="alice"),
            llm=ScriptedLLM(["a"]),
            io="headless",
            session=tmp_path / "old.kohakutr",
            start=True,
        )
        await c.wait_restoration_ready()
        for _ in range(100):
            if c.graph_id in t._drive_runtime._registry._ready_graphs:
                break
            await asyncio.sleep(0.01)
        assert c.graph_id in t._drive_runtime._registry._ready_graphs
        old = t._session_stores[c.graph_id]
        target = tmp_path / "candidate.kohakutr"
        candidate = (
            target if minted else autosession.mint_store(t, c.graph_id, path=target)
        )
        lock = graph_checkpoint._engine_locks(t)[c.graph_id]
        await lock.acquire()
        task = asyncio.create_task(t.attach_session(c.graph_id, candidate))
        try:
            for _ in range(100):
                if c.agent.session_store is not old:
                    break
                await asyncio.sleep(0.01)
            assert c.agent.session_store is not old
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert t._session_stores[c.graph_id] is old
            assert c.agent.session_store is old
            assert not old._closed
            assert c.graph_id in t._owned_sessions
            old.append_event("alice", "user_input", {"content": "after cancellation"})
            old.flush()
            assert t._drive_runtime._registry._bound_stores[c.graph_id] is old
            for _ in range(100):
                if c.graph_id in t._drive_runtime._registry._ready_graphs:
                    break
                await asyncio.sleep(0.01)
            assert c.graph_id in t._drive_runtime._registry._ready_graphs
            if minted:
                reopened = SessionStore(target, writer_lock=True)
                reopened.close(update_status=False)
            else:
                assert (
                    not candidate._closed
                )  # Caller retains failed external candidates.
        finally:
            lock.release()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await t.shutdown()
            if not minted:
                candidate.close(update_status=False)
