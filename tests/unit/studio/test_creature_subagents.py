"""Behavior tests for
:mod:`kohakuterrarium.studio.sessions.creature_subagents`.

Drives the accessor against a REAL :class:`SubAgentManager` (the only
seam is ``find_creature``, patched to return a fake creature whose
``agent`` carries the live manager + a stub session store). Pins the
read modes (live job / live interactive / live running task /
persisted / name-only latest-persisted fallback) and the send paths
(live task messageable, completed run rejected).
"""

import asyncio

import pytest

from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.errors import ConflictError, InvalidRequestError, NotFoundError
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.subagent.manager import SubAgentManager
from kohakuterrarium.studio.sessions import creature_subagents as acc
from kohakuterrarium.testing.llm import ScriptedLLM


class _Config:
    def __init__(self, name):
        self.name = name


class _Agent:
    def __init__(self, manager, store=None, name="controller"):
        self.subagent_manager = manager
        self.session_store = store
        self.config = _Config(name)


class _Creature:
    def __init__(self, agent, name="controller"):
        self.agent = agent
        self.name = name


class _Service:
    """Not a TerrariumService — as_engine returns it unchanged."""


class _Store:
    def __init__(self, conv_json=None, meta=None):
        self._conv = conv_json
        self._meta = meta
        self.queried: list[tuple] = []

    def load_subagent_conversation(self, parent, name, run):
        self.queried.append(("conv", parent, name, run))
        return self._conv

    def load_subagent_meta(self, parent, name, run):
        self.queried.append(("meta", parent, name, run))
        return self._meta

    def find_subagent_run(self, job_id, *, parent=None):
        if not isinstance(self._meta, dict) or self._meta.get("job_id") != job_id:
            return None
        return {
            "parent": parent or "controller",
            "name": "explore",
            "run": 0,
            "job_id": job_id,
            "meta": self._meta,
        }


class _RunStore:
    """Persisted-run store double: ``(parent, name, run) -> (conv_json, meta)``
    plus the ``_subagent_runs`` next-index counter the accessor probes."""

    def __init__(self, parent, name, runs):
        self._data = {(parent, name, r): v for r, v in runs.items()}
        self._subagent_runs = {f"{parent}:{name}": (max(runs) + 1) if runs else 0}

    def load_subagent_conversation(self, parent, name, run):
        entry = self._data.get((parent, name, run))
        return entry[0] if entry else None

    def load_subagent_meta(self, parent, name, run):
        entry = self._data.get((parent, name, run))
        return entry[1] if entry else None

    def find_subagent_run(self, job_id, *, parent=None):
        for (entry_parent, name, run), (_, meta) in self._data.items():
            if parent is not None and entry_parent != parent:
                continue
            if meta.get("job_id") == job_id:
                return {
                    "parent": entry_parent,
                    "name": name,
                    "run": run,
                    "job_id": job_id,
                    "meta": meta,
                }
        return None

    def list_subagent_runs(self, *, parent=None, name=None):
        rows = []
        for (entry_parent, entry_name, run), (_, meta) in self._data.items():
            if parent is not None and entry_parent != parent:
                continue
            if name is not None and entry_name != name:
                continue
            rows.append(
                {
                    "parent": entry_parent,
                    "name": entry_name,
                    "run": run,
                    "job_id": meta.get("job_id"),
                    "meta": meta,
                }
            )
        return rows


def _install(monkeypatch, agent):
    creature = _Creature(agent, name=agent.config.name)
    monkeypatch.setattr(acc, "find_creature", lambda engine, sid, cid: creature)
    return _Service()


async def _spawn(manager, name="explore", task="find the bug", answer="the answer"):
    manager.llm = ScriptedLLM([answer])
    manager.register(SubAgentConfig(name=name, max_turns=1))
    return await manager.spawn(name, task, background=False)


class _PausableLLM:
    """Blocks turn 1 on ``release`` so the run is observably mid-flight;
    later turns return immediately. Emits plain text (no tool calls)."""

    model = "pausable"

    def __init__(self, responses):
        self._responses = responses
        self._calls = 0
        self.turn1_started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat(self, messages, *, stream=True, **kwargs):
        index = self._calls
        self._calls += 1
        if index == 0:
            self.turn1_started.set()
            await self.release.wait()
        yield self._responses[min(index, len(self._responses) - 1)]


class TestReadLiveJob:
    async def test_live_job_conversation(self, monkeypatch):
        manager = SubAgentManager(Registry(), ScriptedLLM(["the answer"]))
        job_id = await _spawn(manager)
        service = _install(monkeypatch, _Agent(manager))
        out = acc.read_subagent_conversation(service, "g", "cid", job_id=job_id)
        assert out["live"] is True
        assert out["interactive"] is False
        # This spawn ran to completion → not still-running → read-only.
        assert out["can_receive"] is False
        assert out["job_id"] == job_id
        # Name is reported from the live handle, not echoed from the query.
        assert out["name"] == "explore"
        joined = " ".join(str(m.get("content", "")) for m in out["messages"])
        assert "find the bug" in joined and "the answer" in joined

    async def test_unknown_job_with_no_store_is_not_found(self, monkeypatch):
        # A job id that isn't live and has no persisted fallback → 404.
        manager = SubAgentManager(Registry(), ScriptedLLM(["x"]))
        service = _install(monkeypatch, _Agent(manager))
        with pytest.raises(NotFoundError):
            acc.read_subagent_conversation(service, "g", "cid", job_id="ghost")


class TestJobIdPersistedFallback:
    async def test_dead_job_id_reads_its_exact_persisted_run(self, monkeypatch):
        first = Conversation()
        first.append("user", "first task")
        first.append("assistant", "first run answer")
        second = Conversation()
        second.append("user", "second task")
        second.append("assistant", "second run answer")
        store = _RunStore(
            "controller",
            "explore",
            {
                0: (
                    first.to_json(),
                    {"turns": 1, "job_id": "agent_explore_a1b2c3d4"},
                ),
                1: (
                    second.to_json(),
                    {"turns": 2, "job_id": "agent_explore_deadbeef"},
                ),
            },
        )
        manager = SubAgentManager(Registry(), ScriptedLLM(["x"]))
        manager._parent_name = "controller"
        agent = _Agent(manager, store=store, name="controller")
        service = _install(monkeypatch, agent)
        out = acc.read_subagent_conversation(
            service, "g", "cid", job_id="agent_explore_a1b2c3d4"
        )
        assert out["live"] is False
        assert out["interactive"] is False
        assert out["run"] == 0
        joined = " ".join(str(m.get("content", "")) for m in out["messages"])
        assert "first run answer" in joined
        assert "second run answer" not in joined

    def test_dead_job_id_reads_one_unambiguous_legacy_run(self, monkeypatch):
        legacy = Conversation()
        legacy.append("assistant", "legacy answer")
        store = _RunStore(
            "controller",
            "explore",
            {0: (legacy.to_json(), {"turns": 1})},
        )
        manager = SubAgentManager(Registry(), ScriptedLLM(["x"]))
        manager._parent_name = "controller"
        service = _install(monkeypatch, _Agent(manager, store=store, name="controller"))
        out = acc.read_subagent_conversation(
            service, "g", "cid", job_id="agent_explore_a1b2c3d4"
        )
        assert out["run"] == 0
        assert out["job_id"] is None

    def test_dead_job_id_rejects_ambiguous_legacy_runs(self, monkeypatch):
        first = Conversation()
        first.append("assistant", "first legacy answer")
        second = Conversation()
        second.append("assistant", "second legacy answer")
        store = _RunStore(
            "controller",
            "explore",
            {
                0: (first.to_json(), {"turns": 1}),
                1: (second.to_json(), {"turns": 2}),
            },
        )
        manager = SubAgentManager(Registry(), ScriptedLLM(["x"]))
        manager._parent_name = "controller"
        service = _install(monkeypatch, _Agent(manager, store=store, name="controller"))
        with pytest.raises(ConflictError, match="multiple legacy runs"):
            acc.read_subagent_conversation(
                service, "g", "cid", job_id="agent_explore_a1b2c3d4"
            )

    def test_dead_job_id_with_no_persisted_run_is_not_found(self, monkeypatch):
        store = _RunStore("controller", "explore", {})
        manager = SubAgentManager(Registry(), ScriptedLLM(["x"]))
        manager._parent_name = "controller"
        service = _install(monkeypatch, _Agent(manager, store=store, name="controller"))
        with pytest.raises(NotFoundError):
            acc.read_subagent_conversation(
                service, "g", "cid", job_id="agent_explore_a1b2c3d4"
            )

    def test_name_with_underscores_extracted_from_job_id(self):
        # Only the trailing 8-hex uuid is stripped; underscored names survive.
        assert (
            acc._subagent_name_from_job_id("agent_memory_read_deadbeef")
            == "memory_read"
        )
        assert acc._subagent_name_from_job_id("not-a-job-id") is None


class TestReadPersisted:
    def test_persisted_run_read(self, monkeypatch):
        conv = Conversation()
        conv.append("system", "sys")
        conv.append("user", "saved task")
        conv.append("assistant", "saved answer")
        store = _Store(conv_json=conv.to_json(), meta={"turns": 2, "success": True})
        manager = SubAgentManager(Registry(), ScriptedLLM(["x"]))
        manager._parent_name = "controller"
        agent = _Agent(manager, store=store, name="controller")
        service = _install(monkeypatch, agent)
        out = acc.read_subagent_conversation(service, "g", "cid", name="explore", run=3)
        assert out["live"] is False
        assert out["can_receive"] is False
        assert out["run"] == 3
        assert out["meta"] == {"turns": 2, "success": True}
        # Persisted read keys on the manager's ``_parent_name``.
        assert ("conv", "controller", "explore", 3) in store.queried
        joined = " ".join(str(m.get("content", "")) for m in out["messages"])
        assert "saved answer" in joined

    def test_name_without_run_falls_back_to_latest_persisted(self, monkeypatch):
        # A resumed block whose event carried the NAME but no job_id must
        # still resolve to the latest saved run for a non-interactive
        # sub-agent — never a bogus 404 when a transcript exists.
        latest = Conversation()
        latest.append("user", "task")
        latest.append("assistant", "latest saved answer")
        older = Conversation()
        older.append("assistant", "old run")
        store = _RunStore(
            "controller",
            "explore",
            {0: (older.to_json(), {"turns": 1}), 1: (latest.to_json(), {"turns": 2})},
        )
        manager = SubAgentManager(Registry(), ScriptedLLM(["x"]))
        manager._parent_name = "controller"
        agent = _Agent(manager, store=store, name="controller")
        service = _install(monkeypatch, agent)
        out = acc.read_subagent_conversation(service, "g", "cid", name="explore")
        assert out["live"] is False
        assert out["interactive"] is False
        assert out["can_receive"] is False
        assert out["run"] == 1
        joined = " ".join(str(m.get("content", "")) for m in out["messages"])
        assert "latest saved answer" in joined

    def test_missing_run_is_not_found(self, monkeypatch):
        store = _Store(conv_json=None, meta=None)
        manager = SubAgentManager(Registry(), ScriptedLLM(["x"]))
        agent = _Agent(manager, store=store)
        service = _install(monkeypatch, agent)
        with pytest.raises(NotFoundError):
            acc.read_subagent_conversation(service, "g", "cid", name="ghost", run=9)

    def test_no_identifier_is_invalid_request(self, monkeypatch):
        manager = SubAgentManager(Registry(), ScriptedLLM(["x"]))
        service = _install(monkeypatch, _Agent(manager))
        with pytest.raises(InvalidRequestError):
            acc.read_subagent_conversation(service, "g", "cid")


class TestInteractive:
    async def test_live_interactive_read_and_send(self, monkeypatch):
        manager = SubAgentManager(Registry(), ScriptedLLM(["parent"]))
        manager.register(SubAgentConfig(name="writer", interactive=True))
        await manager.start_interactive("writer")
        service = _install(monkeypatch, _Agent(manager))
        try:
            read = acc.read_subagent_conversation(service, "g", "cid", name="writer")
            assert read["live"] is True
            assert read["interactive"] is True
            # A live interactive child is messageable.
            assert read["can_receive"] is True
            assert isinstance(read["messages"], list)

            sent = await acc.send_to_subagent(service, "g", "cid", "writer", "go on")
            assert sent == {"status": "sent", "name": "writer", "job_id": None}
        finally:
            await manager.stop_interactive("writer")

    async def test_job_id_of_live_interactive_reports_interactive_true(
        self, monkeypatch
    ):
        # Defensive truthfulness: if a live job handle IS an interactive
        # instance, the read reports interactive=True so the FE chat box
        # can appear.
        from kohakuterrarium.modules.subagent.result import SubAgentJob

        manager = SubAgentManager(Registry(), ScriptedLLM(["x"]))
        manager.register(SubAgentConfig(name="writer", interactive=True))
        inter = await manager.start_interactive("writer")
        job_id = "agent_writer_deadbeef"
        manager._jobs[job_id] = SubAgentJob(inter, job_id)
        service = _install(monkeypatch, _Agent(manager, name="controller"))
        try:
            out = acc.read_subagent_conversation(service, "g", "cid", job_id=job_id)
            assert out["live"] is True
            assert out["interactive"] is True
            assert out["name"] == "writer"
        finally:
            await manager.stop_interactive("writer")

    async def test_send_with_no_live_target_is_rejected(self, monkeypatch):
        # A registered-but-not-running name has no live instance to receive
        # a message → ConflictError, never a fake success.
        manager = SubAgentManager(Registry(), ScriptedLLM(["x"]))
        manager.register(SubAgentConfig(name="writer", interactive=True))
        service = _install(monkeypatch, _Agent(manager))
        with pytest.raises(ConflictError):
            await acc.send_to_subagent(service, "g", "cid", "writer", "hi")

    async def test_name_without_run_and_no_interactive_is_not_found(self, monkeypatch):
        manager = SubAgentManager(Registry(), ScriptedLLM(["x"]))
        service = _install(monkeypatch, _Agent(manager))
        with pytest.raises(NotFoundError):
            acc.read_subagent_conversation(service, "g", "cid", name="writer")


class TestLiveTaskMessaging:
    """A live, still-running *task* sub-agent (not interactive) is the
    case requirement C hinges on: the accessor must report
    ``can_receive=True`` so the FE shows the box, and a send through the
    accessor must reach the run's inbox and land in its conversation."""

    async def test_running_task_is_messageable_via_job_id(self, monkeypatch):
        llm = _PausableLLM(["turn one", "reply to the send"])
        manager = SubAgentManager(Registry(), llm)
        manager.register(SubAgentConfig(name="explore", max_turns=5))
        job_id = await manager.spawn("explore", "the task", background=True)
        await llm.turn1_started.wait()
        service = _install(monkeypatch, _Agent(manager))
        try:
            read = acc.read_subagent_conversation(service, "g", "cid", job_id=job_id)
            # Live one-shot task, mid-run → messageable, though NOT interactive.
            assert read["live"] is True
            assert read["interactive"] is False
            assert read["can_receive"] is True

            sent = await acc.send_to_subagent(
                service, "g", "cid", "explore", "handle this too", job_id=job_id
            )
            assert sent == {
                "status": "sent",
                "name": "explore",
                "job_id": job_id,
            }
        finally:
            llm.release.set()
            await manager.wait_for(job_id)

        joined = " ".join(
            str(m.get("content", ""))
            for m in manager._jobs[job_id].subagent.conversation.to_messages()
        )
        assert "handle this too" in joined


class _MultiNode:
    """Lab-host stand-in: ``host_engine_or_none`` keys off ``connected_nodes``."""

    connected_nodes = ()


class TestMultiNodeDegrades:
    def test_read_degrades_to_not_found(self, monkeypatch):
        # In lab-host mode there is no host agent engine — degrade to
        # NotFoundError instead of letting as_engine raise a 500.
        monkeypatch.setattr(
            acc, "find_creature", lambda *a, **k: pytest.fail("must not resolve")
        )
        with pytest.raises(NotFoundError):
            acc.read_subagent_conversation(_MultiNode(), "g", "cid", job_id="j")

    async def test_send_degrades_to_not_found(self, monkeypatch):
        monkeypatch.setattr(
            acc, "find_creature", lambda *a, **k: pytest.fail("must not resolve")
        )
        with pytest.raises(NotFoundError):
            await acc.send_to_subagent(_MultiNode(), "g", "cid", "writer", "hi")
