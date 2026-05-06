"""Test infrastructure for the Terrarium runtime engine.

Mirrors :class:`kohakuterrarium.testing.TestAgentBuilder` for the
multi-agent surface — lets user code exercise the engine end-to-end
without spinning up real LLM providers.

Example::

    builder = (
        TestTerrariumBuilder()
        .with_creature("alice")
        .with_creature("bob")
        .with_channel("chat")
        .with_connection("alice", "bob", channel="chat")
    )
    async with await builder.build() as t:
        async for chunk in t["alice"].chat("hi"):
            ...
"""

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.engine import Terrarium

# ---------------------------------------------------------------------------
# fake-agent stand-in — reused inside ``TestTerrariumBuilder``
# ---------------------------------------------------------------------------


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
    """Minimal Agent stand-in for unit tests of the engine layer.

    Implements only what :class:`Creature` reads in
    ``start`` / ``stop`` / ``chat`` / ``get_status`` and what
    channel-trigger / output-sink injection touches.
    """

    def __init__(
        self,
        name: str = "fake",
        model: str = "test/model",
        responses: list[str] | None = None,
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

    def set_output_handler(self, handler: Any, replace_default: bool = False) -> None:
        self.output_handlers.append(handler)

    def llm_identifier(self) -> str:
        return self.config.model

    async def start(self) -> None:
        self.is_running = True
        self.start_calls += 1

    async def stop(self) -> None:
        self.is_running = False
        self.stop_calls += 1

    async def inject_input(self, message, *, source: str = "chat") -> None:
        """Record the input and replay the next scripted response (if any)
        through every registered output handler so ``Creature.chat``
        sees text chunks back."""
        self.injected.append((message, source))
        if self.responses and self._chat_index < len(self.responses):
            response = self.responses[self._chat_index]
            self._chat_index += 1
            for handler in self.output_handlers:
                try:
                    handler(response)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# TestTerrariumBuilder — declarative builder for engine tests
# ---------------------------------------------------------------------------


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
    """Declarative builder for a :class:`Terrarium` engine pre-loaded
    with fake-agent creatures, channels, and wiring.

    Each ``with_*`` method returns ``self`` for chaining.  Call
    ``await builder.build()`` to materialise the engine — the engine
    is returned ready-started so tests can ``async with`` it directly.
    """

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
        """Add a creature that will be wrapped around a fake agent."""
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
        """Declare a channel inside the (single) graph."""
        self._channels.append(_ChannelSpec(name=name, description=description))
        return self

    def with_connection(
        self,
        sender: str,
        receiver: str,
        *,
        channel: str | None = None,
    ) -> "TestTerrariumBuilder":
        """Wire ``sender → receiver`` over ``channel`` (or auto-named)."""
        self._connections.append(
            _ConnectionSpec(sender=sender, receiver=receiver, channel=channel)
        )
        return self

    def with_separate_graphs(self) -> "TestTerrariumBuilder":
        """Each creature gets its own singleton graph instead of all
        landing in one shared graph.  Useful for testing cross-graph
        connect (which forces a graph merge)."""
        self._all_in_one_graph = False
        return self

    async def build(self) -> Terrarium:
        """Materialise the engine.  Returns it already started."""
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
