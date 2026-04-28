"""Shared fakes for studio.sessions tests.

Real ``Agent`` instances do too much (LLM init, tool catalog, plugin
discovery) for engine-layer unit tests.  These minimal fakes expose
exactly the slice of the agent surface the studio session helpers
touch — chat, regen, edit, rewind, history, scratchpad, plugins,
jobs, etc.

The fakes here are intentionally separate from
``tests.unit.terrarium._fakes`` because the studio tier reaches deeper
into the agent (plugins, executor, conversation_history) than the
engine-layer tests do.
"""

from datetime import datetime
from types import SimpleNamespace
from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.terrarium.creature_host import Creature


class _FakeJob:
    """Stand-in for ``Job.to_dict``-able job object."""

    def __init__(self, job_id: str, kind: str = "tool") -> None:
        self.job_id = job_id
        self.kind = kind

    def to_dict(self) -> dict:
        return {"job_id": self.job_id, "kind": self.kind}


class _FakeExecutor:
    """Stand-in for ``core.executor.Executor`` for control tests."""

    def __init__(self, working_dir: str = ".") -> None:
        self._working_dir = working_dir
        self._jobs: list[_FakeJob] = []
        self._cancelled: list[str] = []

    def get_running_jobs(self) -> list[_FakeJob]:
        return list(self._jobs)

    def add_job(self, job_id: str) -> None:
        self._jobs.append(_FakeJob(job_id))

    async def cancel(self, job_id: str) -> bool:
        for j in self._jobs:
            if j.job_id == job_id:
                self._jobs = [x for x in self._jobs if x.job_id != job_id]
                self._cancelled.append(job_id)
                return True
        return False


class _FakeSubAgentManager:
    def __init__(self) -> None:
        self._jobs: list[_FakeJob] = []
        self.cancelled: list[str] = []

    def get_running_jobs(self) -> list[_FakeJob]:
        return list(self._jobs)

    def add_job(self, job_id: str) -> None:
        self._jobs.append(_FakeJob(job_id, kind="subagent"))

    async def cancel(self, job_id: str) -> bool:
        for j in self._jobs:
            if j.job_id == job_id:
                self._jobs = [x for x in self._jobs if x.job_id != job_id]
                self.cancelled.append(job_id)
                return True
        return False


class _FakeScratchpad:
    """Stand-in for ``core.scratchpad.Scratchpad``."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def to_dict(self) -> dict[str, str]:
        return dict(self._data)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


class _FakeTriggerInfo:
    def __init__(self, trigger_id: str, trigger_type: str = "timer") -> None:
        self.trigger_id = trigger_id
        self.trigger_type = trigger_type
        self.running = True
        self.created_at = datetime(2024, 1, 1, 12, 0, 0)


class _FakeTriggerManager:
    """Minimal trigger manager that satisfies both engine-layer
    ``inject_channel_trigger`` (which writes into ``_triggers`` /
    ``_created_at`` dicts) and studio-layer ``list_triggers`` (which
    iterates ``list()``)."""

    def __init__(self, triggers: list[_FakeTriggerInfo] | None = None) -> None:
        # Engine layer treats this as a dict keyed by trigger_id; studio
        # layer treats it as iterable info objects via ``list()``.
        self._triggers: dict[str, _FakeTriggerInfo] = {}
        self._created_at: dict = {}
        for info in triggers or []:
            self._triggers[info.trigger_id] = info

    def list(self) -> list[_FakeTriggerInfo]:
        return [
            info
            for info in self._triggers.values()
            if isinstance(info, _FakeTriggerInfo)
        ]


class _FakeNativeToolOptions:
    def __init__(self) -> None:
        self._values: dict[str, dict] = {}

    def list(self) -> dict[str, dict]:
        return dict(self._values)

    def get(self, name: str) -> dict:
        return dict(self._values.get(name, {}))

    def set(self, tool: str, values: dict) -> dict:
        self._values[tool] = dict(values)
        return dict(self._values[tool])


class _FakeWorkspace:
    def __init__(self, path: str = ".") -> None:
        self._path = path

    def get(self) -> str:
        return self._path

    def set(self, new_path: str) -> str:
        self._path = new_path
        return new_path


class _FakePluginManager:
    def __init__(self, plugins: list[dict] | None = None) -> None:
        self._plugins = plugins or [
            {"name": "plug_a", "enabled": True},
            {"name": "plug_b", "enabled": False},
        ]
        self.load_pending_calls = 0

    def list_plugins(self) -> list[dict]:
        return [dict(p) for p in self._plugins]

    def is_enabled(self, name: str) -> bool:
        for p in self._plugins:
            if p["name"] == name:
                return bool(p["enabled"])
        return False

    def enable(self, name: str) -> None:
        for p in self._plugins:
            if p["name"] == name:
                p["enabled"] = True
                return

    def disable(self, name: str) -> None:
        for p in self._plugins:
            if p["name"] == name:
                p["enabled"] = False
                return

    async def load_pending(self) -> None:
        self.load_pending_calls += 1


class _FakeRegistry:
    """Minimal ``ToolRegistry`` for native_tool_inventory tests."""

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    def add_tool(self, name: str, tool: Any) -> None:
        self._tools[name] = tool

    def list_tools(self) -> list[str]:
        return list(self._tools)

    def get_tool(self, name: str) -> Any:
        return self._tools.get(name)


class _FakeOutputRouter:
    def __init__(self) -> None:
        self._secondary_outputs: list[OutputModule] = []
        self.default_output = None

    def add_secondary(self, output: OutputModule) -> None:
        self._secondary_outputs.append(output)

    def remove_secondary(self, output: OutputModule) -> None:
        self._secondary_outputs = [
            o for o in self._secondary_outputs if o is not output
        ]


class _FakeAgent:
    """Minimal stand-in for ``core.agent.Agent`` in studio.sessions tests."""

    def __init__(
        self,
        name: str = "fake",
        model: str = "test/model",
        *,
        with_workspace: bool = True,
        with_native_tool_options: bool = True,
        with_plugins: bool = True,
        with_session_store: Any = None,
    ) -> None:
        self.is_running = False
        self.config = SimpleNamespace(name=name, model=model, pwd=None)
        self.llm = SimpleNamespace(
            model=model,
            provider="test",
            api_key_env="",
            base_url="",
            _profile_max_context=8000,
        )
        self.compact_manager = None
        self.session_store = with_session_store
        self.executor = _FakeExecutor()
        self.subagent_manager = _FakeSubAgentManager()
        self.tools: list[Any] = []
        self.subagents: list[Any] = []
        self._processing_task = None
        self.output_handlers: list[Any] = []
        self.trigger_manager = _FakeTriggerManager()
        self.output_router = _FakeOutputRouter()
        self.scratchpad = _FakeScratchpad()
        self.workspace = _FakeWorkspace() if with_workspace else None
        self.native_tool_options = (
            _FakeNativeToolOptions() if with_native_tool_options else None
        )
        self.plugins = _FakePluginManager() if with_plugins else None
        self.registry = _FakeRegistry()
        self.conversation_history: list[dict] = []
        self.session = None
        self._working_dir = "."
        # Recording channels for assertions
        self.injected: list[tuple[Any, str]] = []
        self.start_calls = 0
        self.stop_calls = 0
        self.regen_calls = 0
        self.edit_calls: list[tuple] = []
        self.rewind_calls: list[int] = []
        self.interrupt_calls = 0
        self.switched_to: str | None = None
        self.system_prompt_text = "You are fake."

    def set_output_handler(self, handler: Any, replace_default: bool = False) -> None:
        self.output_handlers.append(handler)

    def llm_identifier(self) -> str:
        return "test/model"

    def get_system_prompt(self) -> str:
        return self.system_prompt_text

    async def start(self) -> None:
        self.is_running = True
        self.start_calls += 1

    async def stop(self) -> None:
        self.is_running = False
        self.stop_calls += 1

    async def inject_input(self, message, *, source: str = "chat") -> None:
        self.injected.append((message, source))

    async def regenerate_last_response(self) -> None:
        self.regen_calls += 1

    async def edit_and_rerun(
        self,
        msg_idx: int,
        content: str,
        *,
        turn_index: int | None = None,
        user_position: int | None = None,
    ) -> bool:
        self.edit_calls.append((msg_idx, content, turn_index, user_position))
        return True

    async def rewind_to(self, msg_idx: int) -> None:
        self.rewind_calls.append(msg_idx)

    def interrupt(self) -> None:
        self.interrupt_calls += 1

    def switch_model(self, profile_name: str) -> str:
        self.switched_to = profile_name
        return profile_name

    def attach_session_store(self, store: Any) -> None:
        self.session_store = store

    # Hooks consumed by cancel_job / promote_job
    def _interrupt_direct_job(self, job_id: str) -> bool:
        return False

    def _promote_handle(self, job_id: str) -> bool:
        return job_id == "promote-me"


def make_creature(name: str = "fake", **agent_kwargs: Any) -> Creature:
    """Build a :class:`Creature` wrapping a fake agent."""
    agent = _FakeAgent(name=name, **agent_kwargs)
    return Creature(creature_id=name, name=name, agent=agent)


async def install_fake_creature(engine, name: str = "fake", **agent_kwargs: Any):
    """Add a pre-built fake creature into ``engine`` and return it."""
    creature = make_creature(name=name, **agent_kwargs)
    return await engine.add_creature(creature)


def stub_chat_iter(creature: Creature, chunks: list[str]) -> None:
    """Replace ``creature.chat`` with one that yields ``chunks``."""

    async def _chat(message):
        for c in chunks:
            yield c
        # Touch input recorder for assertions.
        creature.agent.injected.append((message, "chat"))

    creature.chat = _chat  # type: ignore[method-assign]


__all__ = [
    "_FakeAgent",
    "_FakeExecutor",
    "_FakeJob",
    "_FakeNativeToolOptions",
    "_FakeOutputRouter",
    "_FakePluginManager",
    "_FakeRegistry",
    "_FakeScratchpad",
    "_FakeSubAgentManager",
    "_FakeTriggerInfo",
    "_FakeTriggerManager",
    "_FakeWorkspace",
    "install_fake_creature",
    "make_creature",
    "stub_chat_iter",
]
