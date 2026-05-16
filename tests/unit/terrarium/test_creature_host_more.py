"""Coverage tests for the uncovered branches of
:mod:`kohakuterrarium.terrarium.creature_host`.

Adds: drive_input spawn, _reap_input_task cancel/timeout/exception
paths, chat queue drain, get_status branches, log helpers,
build_creature dispatch, _safe_creature_id edge cases.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.terrarium import creature_host as ch_mod
from kohakuterrarium.terrarium.creature_host import (
    Creature,
    _safe_creature_id,
    build_creature,
)
from kohakuterrarium.testing.terrarium import _FakeAgent

# ── _safe_creature_id ─────────────────────────────────────────


class TestSafeCreatureId:
    def test_basic_name(self):
        out = _safe_creature_id("alice")
        assert out.startswith("alice_")

    def test_special_chars_sanitised(self):
        out = _safe_creature_id("a/b c")
        assert "/" not in out
        assert " " not in out

    def test_empty_name_uses_default(self):
        out = _safe_creature_id("")
        assert out.startswith("creature_")


# ── build_creature dispatch ───────────────────────────────────


class TestBuildCreatureDispatch:
    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError, match="unsupported config type"):
            build_creature(42)  # type: ignore[arg-type]

    def test_str_path_via_monkeypatch(self, monkeypatch, tmp_path):
        cfg_file = tmp_path / "agent.yaml"
        cfg_file.write_text("name: alice\n")

        fake_agent = _FakeAgent(name="alice")
        fake_agent.config = SimpleNamespace(name="alice")

        def _from_path(
            p,
            session=None,
            environment=None,
            llm_override=None,
            pwd=None,
            input_module=None,
        ):
            # ``build_creature`` threads ``input_module`` to every branch
            # (None unless ``suppress_io``).
            return fake_agent

        monkeypatch.setattr(ch_mod.Agent, "from_path", staticmethod(_from_path))
        out = build_creature(str(cfg_file))
        assert out.name == "alice"

    def test_agent_config_path(self, monkeypatch):
        from kohakuterrarium.core.config_types import AgentConfig

        captured = {}

        def _agent_ctor(
            cfg,
            session=None,
            environment=None,
            llm_override=None,
            pwd=None,
            input_module=None,
        ):
            # ``build_creature`` now passes ``input_module`` to every
            # branch (None unless ``suppress_io``) so the IO-suppression
            # contract is uniform.
            captured["cfg"] = cfg
            captured["input_module"] = input_module
            a = _FakeAgent(name=cfg.name)
            a.config = SimpleNamespace(name=cfg.name)
            return a

        monkeypatch.setattr(ch_mod, "Agent", _agent_ctor)
        cfg = AgentConfig(name="bob", system_prompt="hi", llm_profile="default")
        out = build_creature(cfg)
        assert out.name == "bob"
        assert captured["cfg"] is cfg
        # Default spawn does NOT suppress IO — input_module stays None.
        assert captured["input_module"] is None
        # ...but ``suppress_io=True`` forces a NoneInput override.
        captured.clear()
        build_creature(cfg, suppress_io=True)
        assert type(captured["input_module"]).__name__ == "NoneInput"

    def test_creature_config_path(self, monkeypatch):
        from kohakuterrarium.terrarium.config import CreatureConfig

        def _agent_ctor(
            cfg,
            input_module=None,
            session=None,
            environment=None,
            llm_override=None,
            pwd=None,
        ):
            a = _FakeAgent(name=cfg.name)
            a.config = SimpleNamespace(name=cfg.name)
            return a

        from kohakuterrarium.core.config_types import AgentConfig

        monkeypatch.setattr(ch_mod, "Agent", _agent_ctor)
        monkeypatch.setattr(
            ch_mod,
            "build_agent_config",
            lambda data, base_dir: AgentConfig(name="bob", system_prompt="x"),
        )
        cfg = CreatureConfig(
            name="bob",
            config_data={"name": "bob"},
            base_dir=Path("."),
            listen_channels=["chat"],
            send_channels=["report"],
        )
        out = build_creature(cfg)
        assert out.listen_channels == ["chat"]
        assert out.send_channels == ["report"]


# ── get_status branches ───────────────────────────────────────


class TestGetStatusBranches:
    def test_status_with_session_store(self):
        agent = _FakeAgent()
        agent.session_store = SimpleNamespace(load_meta=lambda: {"session_id": "sid-x"})
        c = Creature(creature_id="c", name="alice", agent=agent)
        out = c.get_status()
        assert out["session_id"] == "sid-x"

    def test_status_session_store_load_fails(self):
        agent = _FakeAgent()

        def _boom():
            raise RuntimeError("nope")

        agent.session_store = SimpleNamespace(load_meta=_boom)
        c = Creature(creature_id="c", name="alice", agent=agent)
        out = c.get_status()
        assert out["session_id"] == ""

    def test_status_with_executor(self):
        agent = _FakeAgent()
        agent.executor = SimpleNamespace(_working_dir="/work")
        c = Creature(creature_id="c", name="alice", agent=agent)
        out = c.get_status()
        assert out["pwd"] == "/work"

    def test_status_llm_identifier_callable(self):
        agent = _FakeAgent()
        agent.llm_identifier = lambda: "model/x:v1"
        c = Creature(creature_id="c", name="alice", agent=agent)
        out = c.get_status()
        assert out["llm_name"] == "model/x:v1"

    def test_status_llm_identifier_raises(self):
        agent = _FakeAgent()

        def _boom():
            raise RuntimeError("bad")

        agent.llm_identifier = _boom
        c = Creature(creature_id="c", name="alice", agent=agent)
        out = c.get_status()
        assert out["llm_name"] == ""

    def test_status_profile_data_with_api_key_env(self):
        agent = _FakeAgent()
        agent.llm.api_key_env = "OPENAI_API_KEY"
        agent.llm.base_url = "https://api.example.com"
        c = Creature(creature_id="c", name="alice", agent=agent)
        out = c.get_status()
        # Just confirm it doesn't crash and returns provider info.
        assert "provider" in out

    def test_status_with_compact_manager(self):
        agent = _FakeAgent()
        agent.compact_manager = SimpleNamespace(config=SimpleNamespace(threshold=0.5))
        c = Creature(creature_id="c", name="alice", agent=agent)
        out = c.get_status()
        assert out["compact_threshold"] == 4000  # 8000 * 0.5


# ── log helpers ───────────────────────────────────────────────


class TestLogHelpers:
    def test_log_entries_none(self):
        c = Creature(creature_id="c", name="alice", agent=_FakeAgent())
        assert c.get_log_entries() == []

    def test_log_text_none(self):
        c = Creature(creature_id="c", name="alice", agent=_FakeAgent())
        assert c.get_log_text() == ""

    def test_log_entries_with_log(self):
        fake_log = SimpleNamespace(
            get_entries=lambda last_n: ["e1", "e2"],
            get_text=lambda last_n: "text",
        )
        c = Creature(creature_id="c", name="alice", agent=_FakeAgent())
        c.output_log = fake_log
        assert c.get_log_entries() == ["e1", "e2"]
        assert c.get_log_text() == "text"


# ── start() with drive_input ─────────────────────────────────


class TestStartDriveInput:
    async def test_start_spawns_drive_task(self):
        agent = _FakeAgent()
        drive_done = asyncio.Event()

        async def _drive():
            await asyncio.sleep(0)
            drive_done.set()

        agent._drive_input = _drive
        c = Creature(creature_id="c", name="alice", agent=agent)
        await c.start()
        # Yield to let the drive task run.
        await asyncio.sleep(0.05)
        assert drive_done.is_set()
        await c.stop()

    async def test_start_idempotent(self):
        agent = _FakeAgent()
        c = Creature(creature_id="c", name="alice", agent=agent)
        await c.start()
        await c.start()
        assert agent.start_calls == 1
        await c.stop()


# ── _on_input_task_done ──────────────────────────────────────


class TestOnInputTaskDone:
    async def test_cancelled_task_flips_running(self):
        c = Creature(creature_id="c", name="alice", agent=_FakeAgent())
        c._running = True

        async def _coro():
            await asyncio.sleep(10)

        task = asyncio.create_task(_coro())
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        c._on_input_task_done(task)
        assert c._running is False

    async def test_exception_logs_and_flips(self):
        c = Creature(creature_id="c", name="alice", agent=_FakeAgent())
        c._running = True

        async def _coro():
            raise RuntimeError("boom")

        task = asyncio.create_task(_coro())
        try:
            await task
        except RuntimeError:
            pass
        c._on_input_task_done(task)
        assert c._running is False

    async def test_clean_exit_flips_running(self):
        c = Creature(creature_id="c", name="alice", agent=_FakeAgent())
        c._running = True

        async def _coro():
            return None

        task = asyncio.create_task(_coro())
        await task
        c._on_input_task_done(task)
        assert c._running is False


# ── _reap_input_task ─────────────────────────────────────────


class TestReapInputTask:
    async def test_none_returns_early(self):
        c = Creature(creature_id="c", name="alice", agent=_FakeAgent())
        c._input_task = None
        await c._reap_input_task()

    async def test_done_task_returns(self):
        c = Creature(creature_id="c", name="alice", agent=_FakeAgent())

        async def _coro():
            return None

        task = asyncio.create_task(_coro())
        await task
        c._input_task = task
        await c._reap_input_task()
        assert c._input_task is None

    async def test_clean_wait(self):
        c = Creature(creature_id="c", name="alice", agent=_FakeAgent())

        async def _coro():
            await asyncio.sleep(0.01)

        task = asyncio.create_task(_coro())
        c._input_task = task
        await c._reap_input_task()
        assert c._input_task is None

    async def test_raises_inside_wait_swallowed(self):
        c = Creature(creature_id="c", name="alice", agent=_FakeAgent())

        async def _coro():
            await asyncio.sleep(0)
            raise RuntimeError("oops")

        task = asyncio.create_task(_coro())
        c._input_task = task
        await c._reap_input_task()
        assert c._input_task is None


# ── chat queue drain ─────────────────────────────────────────


class TestChatQueueDrain:
    async def test_chat_drains_after_inject(self):
        agent = _FakeAgent(responses=["hello"])
        c = Creature(creature_id="c", name="alice", agent=agent)
        chunks = []
        async for chunk in c.chat("hi"):
            chunks.append(chunk)
        assert "hello" in chunks

    async def test_chat_pipe_clears_stale_chunks(self):
        agent = _FakeAgent(responses=["new"])
        c = Creature(creature_id="c", name="alice", agent=agent)
        c._ensure_chat_pipe()
        # Pre-populate the queue with stale data.
        c._output_queue.put_nowait("stale-1")
        c._output_queue.put_nowait("stale-2")
        chunks = []
        async for chunk in c.chat("hi"):
            chunks.append(chunk)
        assert "stale-1" not in chunks
        assert "new" in chunks
