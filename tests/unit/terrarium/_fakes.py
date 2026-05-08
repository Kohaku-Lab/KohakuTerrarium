"""Shared test fakes for terrarium engine tests.

Real ``Agent`` instances do too much (load LLM profiles, parse skills,
etc.) for unit tests of the engine layer.  ``_FakeAgent`` exposes
exactly the slice of the agent surface the engine touches:
``start`` / ``stop`` / ``is_running`` / ``set_output_handler`` /
``inject_input`` / ``_drive_input`` plus enough attributes for
``Creature.get_status`` and channel-trigger / output-sink injection.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.terrarium.creature_host import Creature


class _FakeTriggerManager:
    """Just enough of ``core.trigger_manager.TriggerManager`` for
    ``terrarium.channels.inject_channel_trigger`` to bookkeep against.
    """

    def __init__(self) -> None:
        self._triggers: dict[str, Any] = {}
        self._created_at: dict[str, datetime] = {}


class _FakeOutputRouter:
    """Replicates ``OutputRouter.add_secondary`` / ``remove_secondary``.
    Tests don't dispatch through the router, so we only mimic the list.
    """

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
    """Minimal stand-in for ``core.agent.Agent`` in unit tests."""

    def __init__(
        self,
        name: str = "fake",
        model: str = "test/model",
    ) -> None:
        self.is_running = False
        self._running = False
        self.config = SimpleNamespace(
            name=name, model=model, pwd=None, output_wiring=[]
        )
        self.llm = SimpleNamespace(
            model=model,
            provider="test",
            api_key_env="",
            base_url="",
            _profile_max_context=8000,
        )
        self.compact_manager = None
        self.session_store = None
        self.executor = None
        self.tools: list[Any] = []
        self.subagents: list[Any] = []
        self._processing_task = None
        self.output_handlers: list[Any] = []
        self.trigger_manager = _FakeTriggerManager()
        self.output_router = _FakeOutputRouter()
        self.injected: list[tuple[Any, str]] = []
        self.received_events: list[Any] = []
        self.start_calls = 0
        self.stop_calls = 0
        self.drive_input_calls = 0
        self._drive_input_stop: asyncio.Event = asyncio.Event()

    def set_output_handler(self, handler: Any, replace_default: bool = False) -> None:
        self.output_handlers.append(handler)

    def llm_identifier(self) -> str:
        return "test/model"

    async def start(self) -> None:
        self.is_running = True
        self._running = True
        self.start_calls += 1
        self._drive_input_stop.clear()

    async def stop(self) -> None:
        self.is_running = False
        self._running = False
        self.stop_calls += 1
        self._drive_input_stop.set()

    async def _drive_input(self) -> None:
        """Stand-in for ``Agent._drive_input``: block until ``stop``."""
        self.drive_input_calls += 1
        await self._drive_input_stop.wait()

    async def inject_input(self, message, *, source: str = "chat") -> None:
        self.injected.append((message, source))

    async def _process_event(self, event) -> None:
        self.received_events.append(event)


def make_creature(name: str = "fake", **kwargs) -> Creature:
    """Build a :class:`Creature` wrapping a fake agent."""
    agent = _FakeAgent(name=name, **kwargs)
    return Creature(creature_id=name, name=name, agent=agent)
