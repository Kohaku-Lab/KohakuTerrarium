"""Unit tests for the engine-backed convenience constructors in
:mod:`kohakuterrarium.compose.agent` — ``agent()``, ``factory()``,
and the ``_engine_session_from_config`` / ``_engine_session_from_path``
helpers they delegate to.

The compose runnables consume the chat-session protocol; the
convenience constructors adapt a real :class:`Terrarium` creature into
that shape. These tests exercise the real adapter against a real
engine, with the LLM monkeypatched to a deterministic ``ScriptedLLM``
(the one allowed seam — a live provider is not deterministic).
"""

from pathlib import Path

import pytest

from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.compose.agent import (
    AgentFactory,
    AgentRunnable,
    agent,
    factory,
)
from kohakuterrarium.core.config_types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.testing.llm import ScriptedLLM

_REPLY = "compose-engine-session reply"

_CONFIG_YAML = """\
name: composed
system_prompt: "You are a deterministic compose test agent."
input:
  type: none
output:
  type: stdout
"""


@pytest.fixture(autouse=True)
def _scripted_llm(monkeypatch):
    def _fake_create(config, llm_override=None):
        return ScriptedLLM([_REPLY, _REPLY])

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)


def _config(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        name="composed",
        system_prompt="You are a deterministic compose test agent.",
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="stdout"),
        include_hints_in_prompt=False,
    )


class TestAgentConstructorFromConfig:
    async def test_agent_builds_a_persistent_runnable_from_config(self, tmp_path):
        """``agent(AgentConfig)`` routes through
        ``_engine_session_from_config``: it stands up a real engine +
        creature and returns a started ``AgentRunnable`` whose
        ``agent_id`` is the live creature's id."""
        a = await agent(_config(tmp_path))
        try:
            assert isinstance(a, AgentRunnable)
            # The runnable is backed by the real engine creature.
            assert a._session.agent_id  # a real minted creature id
        finally:
            await a.close()

    async def test_agent_runnable_runs_a_turn(self, tmp_path):
        """The constructed runnable actually drives the creature: a
        turn returns the scripted assistant reply."""
        async with await agent(_config(tmp_path)) as a:
            out = await a.run("hello")
        assert out == _REPLY


class TestAgentConstructorFromPath:
    async def test_factory_from_path_creates_and_tears_down_per_call(self, tmp_path):
        """``factory(path)`` routes through
        ``_engine_session_from_path``: each ``run`` builds a fresh
        engine+creature from the on-disk config and shuts it down
        after. Two calls each return the scripted reply."""
        agent_dir = tmp_path / "composed"
        agent_dir.mkdir()
        (agent_dir / "config.yaml").write_text(_CONFIG_YAML, encoding="utf-8")

        f = factory(str(agent_dir))
        assert isinstance(f, AgentFactory)
        assert await f.run("first") == _REPLY
        assert await f.run("second") == _REPLY

    async def test_agent_from_path_builds_runnable(self, tmp_path):
        """``agent(path)`` also takes the path branch of the
        constructor and yields a working persistent runnable."""
        agent_dir = tmp_path / "composed"
        agent_dir.mkdir()
        (agent_dir / "config.yaml").write_text(_CONFIG_YAML, encoding="utf-8")

        async with await agent(str(agent_dir)) as a:
            assert isinstance(a, AgentRunnable)
            assert await a.run("hi") == _REPLY
