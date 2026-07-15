"""Build Terrarium engine test fixtures with lightweight fake agents."""

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.engine import Terrarium

class _FakeTriggerManager:
    def __init__(self) -> None:
        self._triggers: dict[str, Any] = {}
        self._created_at: dict[str, Any] = {}


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
    """Implement only the Agent surface consumed by engine-layer tests."""

    def __init__(
        self,
        name: str = "fake",
        model: str = "test/model",
        responses: list[str] | None = None,
    ) -> None:
        # Both private and public running-state readers must agree.
        self._running = False
        self.config = SimpleNamespace(name=name, model=model, pwd=None)
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
        self.trigger_manager = _FakeTriggerManager()
        self.output_router = _FakeOutputRouter()
        self.output_handlers: list[Any] = []
        self.injected: list[tuple[Any, str]] = []
        self.responses = list(responses or [])
        self._chat_index = 0
        self.start_calls = 0
        self.stop_calls = 0
        self.processed_events: list[Any] = []

    @property
    def is_running(self) -> bool:
        """Expose the same read-only running state as a real Agent."""
        return self._running

    def set_output_handler(self, handler: Any, replace_default: bool = False) -> None:
        self.output_handlers.append(handler)

    def llm_identifier(self) -> str:
        return self.config.model

    async def start(self) -> None:
        self._running = True
        self.start_calls += 1

    async def stop(self) -> None:
        self._running = False
        self.stop_calls += 1

    def attach_session_store(
        self, store: Any, *, capture_activity: bool = True
    ) -> None:
        """Retain the attached store without installing production output sinks."""
        self.session_store = store

    async def _process_event(self, event: Any) -> None:
        """Record synthetic events delivered by group send or output wiring."""
        self.processed_events.append(event)

    async def inject_input(self, message, *, source: str = "chat") -> None:
        """Record input and replay the next response through output handlers."""
        self.injected.append((message, source))
        if self.responses and self._chat_index < len(self.responses):
            response = self.responses[self._chat_index]
            self._chat_index += 1
            for handler in self.output_handlers:
                try:
                    handler(response)
                except Exception:
                    pass


@dataclass
class _CreatureSpec:
    name: str
    responses: list[str] = field(default_factory=list)


@dataclass
class _ChannelSpec:
    name: str
    description: str = ""


@dataclass
class _ConnectionSpec:
    sender: str
    receiver: str
    channel: str | None = None


class TestTerrariumBuilder:
    """Build a Terrarium preloaded with fake creatures, channels, and wiring.

    ``__test__ = False`` prevents pytest from collecting this public helper.
    """

    __test__ = False

    def __init__(self) -> None:
        self._creatures: list[_CreatureSpec] = []
        self._channels: list[_ChannelSpec] = []
        self._connections: list[_ConnectionSpec] = []
        self._all_in_one_graph = True

    def with_creature(
        self,
        name: str,
        *,
        responses: list[str] | None = None,
    ) -> "TestTerrariumBuilder":
        """Add a fake-agent creature with optional scripted responses."""
        self._creatures.append(
            _CreatureSpec(name=name, responses=list(responses or []))
        )
        return self

    def with_channel(
        self,
        name: str,
        *,
        description: str = "",
    ) -> "TestTerrariumBuilder":
        """Declare a channel in the shared graph."""
        self._channels.append(_ChannelSpec(name=name, description=description))
        return self

    def with_connection(
        self,
        sender: str,
        receiver: str,
        *,
        channel: str | None = None,
    ) -> "TestTerrariumBuilder":
        """Connect two creatures over a named or generated channel."""
        self._connections.append(
            _ConnectionSpec(sender=sender, receiver=receiver, channel=channel)
        )
        return self

    def with_separate_graphs(self) -> "TestTerrariumBuilder":
        """Place each creature in a singleton graph before applying connections."""
        self._all_in_one_graph = False
        return self

    async def build(self) -> Terrarium:
        """Materialize and return the configured engine."""
        engine = Terrarium()
        first_graph_id: str | None = None
        for spec in self._creatures:
            agent = _FakeAgent(name=spec.name, responses=spec.responses)
            creature = Creature(creature_id=spec.name, name=spec.name, agent=agent)
            graph = (
                first_graph_id
                if self._all_in_one_graph and first_graph_id is not None
                else None
            )
            added = await engine.add_creature(creature, graph=graph)
            if first_graph_id is None:
                first_graph_id = added.graph_id
        if first_graph_id is not None:
            for ch in self._channels:
                await engine.add_channel(
                    first_graph_id,
                    ch.name,
                    description=ch.description,
                )
        for c in self._connections:
            await engine.connect(c.sender, c.receiver, channel=c.channel)
        return engine
