"""Tests for the multi-creature slash commands shipped by topic 08.

Covers ``/stop``, ``/start``, ``/jobs``, ``/channels``, ``/scratchpad``,
``/spawn``. Uses lightweight fakes for the engine + creature so the
tests stay at the command level — engine internals are exercised by
the integration suite.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.user_commands import (
    ChannelsCommand,
    JobsCommand,
    ScratchpadCommand,
    SpawnCommand,
    StartCommand,
    StopCommand,
)
from kohakuterrarium.modules.user_command.base import UserCommandContext


class _FakeCreature:
    def __init__(self, *, creature_id, name, running=True, privileged=False):
        self.creature_id = creature_id
        self.name = name
        self._running = running
        self.is_privileged = privileged
        self.listen_channels = []
        self.send_channels = []
        self.agent = SimpleNamespace(executor=None, scratchpad=None)
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self):
        self.start_calls += 1
        self._running = True

    async def stop(self):
        self.stop_calls += 1
        self._running = False


class _FakeEngine:
    def __init__(self, creatures):
        self._by_id = {c.creature_id: c for c in creatures}
        self.added: list[str] = []

    def get_creature(self, cid):
        if cid not in self._by_id:
            raise KeyError(cid)
        return self._by_id[cid]

    def list_creatures(self):
        return list(self._by_id.values())

    async def add_creature(self, recipe):
        self.added.append(recipe)
        new = _FakeCreature(creature_id="spawned", name=recipe)
        self._by_id["spawned"] = new
        return new


def _ctx(*, agent=None, engine=None, creature_id=""):
    return UserCommandContext(
        agent=agent,
        extra={"engine": engine, "creature_id": creature_id},
    )


# ── /stop ────────────────────────────────────────────────────────────


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_focused_creature(self):
        c = _FakeCreature(creature_id="c1", name="alice")
        eng = _FakeEngine([c])
        result = await StopCommand().execute(
            "", _ctx(agent=c.agent, engine=eng, creature_id="c1")
        )
        assert result.success
        assert "Stopped alice" in result.output
        assert c.stop_calls == 1

    @pytest.mark.asyncio
    async def test_stop_named_creature(self):
        c1 = _FakeCreature(creature_id="c1", name="alice")
        c2 = _FakeCreature(creature_id="c2", name="bob")
        eng = _FakeEngine([c1, c2])
        result = await StopCommand().execute(
            "bob", _ctx(agent=c1.agent, engine=eng, creature_id="c1")
        )
        assert result.success
        assert c2.stop_calls == 1
        assert c1.stop_calls == 0

    @pytest.mark.asyncio
    async def test_stop_unknown_name_errors(self):
        c = _FakeCreature(creature_id="c1", name="alice")
        eng = _FakeEngine([c])
        result = await StopCommand().execute(
            "nobody", _ctx(agent=c.agent, engine=eng, creature_id="c1")
        )
        assert not result.success
        assert "unknown" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_stop_already_stopped(self):
        c = _FakeCreature(creature_id="c1", name="alice", running=False)
        eng = _FakeEngine([c])
        result = await StopCommand().execute(
            "", _ctx(agent=c.agent, engine=eng, creature_id="c1")
        )
        assert result.success
        assert "already stopped" in result.output


# ── /start ───────────────────────────────────────────────────────────


class TestStart:
    @pytest.mark.asyncio
    async def test_start_a_stopped_creature(self):
        c = _FakeCreature(creature_id="c1", name="alice", running=False)
        eng = _FakeEngine([c])
        result = await StartCommand().execute(
            "", _ctx(agent=c.agent, engine=eng, creature_id="c1")
        )
        assert result.success
        assert c.start_calls == 1
        assert c.is_running

    @pytest.mark.asyncio
    async def test_start_already_running_is_noop(self):
        c = _FakeCreature(creature_id="c1", name="alice", running=True)
        eng = _FakeEngine([c])
        result = await StartCommand().execute(
            "", _ctx(agent=c.agent, engine=eng, creature_id="c1")
        )
        assert result.success
        assert "already running" in result.output
        assert c.start_calls == 0


# ── /jobs ────────────────────────────────────────────────────────────


class TestJobs:
    @pytest.mark.asyncio
    async def test_no_executor_shows_message(self):
        c = _FakeCreature(creature_id="c1", name="alice")
        result = await JobsCommand().execute("", _ctx(agent=c.agent))
        assert result.success
        assert "executor unavailable" in result.output.lower()

    @pytest.mark.asyncio
    async def test_empty_jobs_list(self):
        c = _FakeCreature(creature_id="c1", name="alice")
        c.agent.executor = SimpleNamespace(get_running_jobs=lambda: [])
        result = await JobsCommand().execute("", _ctx(agent=c.agent))
        assert "No running jobs" in result.output

    @pytest.mark.asyncio
    async def test_lists_each_job(self):
        c = _FakeCreature(creature_id="c1", name="alice")
        jobs = [
            SimpleNamespace(job_id="j1", name="bash", kind="tool"),
            SimpleNamespace(job_id="j2", name="explore", kind="subagent"),
        ]
        c.agent.executor = SimpleNamespace(get_running_jobs=lambda: jobs)
        result = await JobsCommand().execute("", _ctx(agent=c.agent))
        assert "bash" in result.output
        assert "explore" in result.output


# ── /channels ────────────────────────────────────────────────────────


class TestChannels:
    @pytest.mark.asyncio
    async def test_no_channels_message(self):
        c = _FakeCreature(creature_id="c1", name="alice")
        eng = _FakeEngine([c])
        result = await ChannelsCommand().execute("", _ctx(engine=eng, creature_id="c1"))
        assert "no channels" in result.output.lower()

    @pytest.mark.asyncio
    async def test_lists_listen_and_send(self):
        c = _FakeCreature(creature_id="c1", name="alice")
        c.listen_channels = ["alpha", "beta"]
        c.send_channels = ["alpha"]
        eng = _FakeEngine([c])
        result = await ChannelsCommand().execute("", _ctx(engine=eng, creature_id="c1"))
        assert "alpha" in result.output
        assert "beta" in result.output
        assert "listen" in result.output
        assert "send" in result.output


# ── /scratchpad ──────────────────────────────────────────────────────


class TestScratchpad:
    @pytest.mark.asyncio
    async def test_no_scratchpad(self):
        c = _FakeCreature(creature_id="c1", name="alice")
        result = await ScratchpadCommand().execute("", _ctx(agent=c.agent))
        assert result.success
        assert "No scratchpad" in result.output

    @pytest.mark.asyncio
    async def test_renders_scratchpad_text(self):
        c = _FakeCreature(creature_id="c1", name="alice")
        c.agent.scratchpad = SimpleNamespace(get_all=lambda: "alpha\nbeta\ngamma")
        result = await ScratchpadCommand().execute("", _ctx(agent=c.agent))
        assert "alpha" in result.output and "gamma" in result.output

    @pytest.mark.asyncio
    async def test_truncates_long_scratchpad(self):
        c = _FakeCreature(creature_id="c1", name="alice")
        c.agent.scratchpad = SimpleNamespace(
            get_all=lambda: "\n".join(f"L{i}" for i in range(200))
        )
        result = await ScratchpadCommand().execute("", _ctx(agent=c.agent))
        assert "more lines" in result.output


# ── /spawn ───────────────────────────────────────────────────────────


class TestSpawn:
    @pytest.mark.asyncio
    async def test_unprivileged_focus_rejects_spawn(self):
        c = _FakeCreature(creature_id="c1", name="alice", privileged=False)
        eng = _FakeEngine([c])
        result = await SpawnCommand().execute(
            "examples/worker", _ctx(engine=eng, creature_id="c1")
        )
        assert not result.success
        assert "privileged" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_privileged_focus_spawns(self):
        c = _FakeCreature(creature_id="c1", name="alice", privileged=True)
        eng = _FakeEngine([c])
        result = await SpawnCommand().execute(
            "examples/worker", _ctx(engine=eng, creature_id="c1")
        )
        assert result.success
        assert eng.added == ["examples/worker"]

    @pytest.mark.asyncio
    async def test_missing_recipe_arg(self):
        c = _FakeCreature(creature_id="c1", name="alice", privileged=True)
        eng = _FakeEngine([c])
        result = await SpawnCommand().execute("", _ctx(engine=eng, creature_id="c1"))
        assert not result.success
        assert "usage" in (result.error or "").lower()
