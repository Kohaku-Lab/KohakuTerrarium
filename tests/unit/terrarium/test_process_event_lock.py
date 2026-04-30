"""Regression tests for the ``_process_event`` lock (bug #2).

Pre-fix, ``Agent._process_event`` performed a sizeable block of side
effects BEFORE acquiring ``self._processing_lock``:

* turn / branch counter bookkeeping
* ``session_store.append_event`` (twice)
* ``output_router.on_user_input``  (TUI render path)
* ``plugins.notify("on_event", ...)``
* ``inject_skill_path_hint`` (mutates the agent's controller prompt)

When two trigger tasks called ``_process_event`` near-simultaneously,
all of the above would interleave between the two calls — the TUI
would mid-stream a second user bubble, the session log got
out-of-order events, and skill-hint mutation could race.

Post-fix the entire body is wrapped in the lock.  These tests verify:

1. Two concurrent ``_process_event`` calls serialize end-to-end —
   no overlap of pre-controller side effects.
2. The pre-controller side effects of the SECOND call only run AFTER
   the first call's controller stage completes.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from kohakuterrarium.core.agent_handlers import AgentHandlersMixin
from kohakuterrarium.core.events import TriggerEvent


class _FakeAgent:
    """Lightweight stand-in for ``Agent`` exposing just the surface
    that ``AgentHandlersMixin._process_event`` touches.

    We want to exercise the *real* ``_process_event`` source (so the
    lock guard gets tested) without spinning the LLM / executor stack.
    Pulling the unbound method off the mixin and calling it with this
    object as ``self`` is enough.
    """

    def __init__(self) -> None:
        self._processing_lock = asyncio.Lock()
        self._running = True
        self._turn_index = 0
        self._branch_id = 0
        self._parent_branch_path: list = []
        self.session_store = None
        self.plugins = None
        self.output_router = _FakeOutputRouter(self)
        self.controller = SimpleNamespace()
        self.config = SimpleNamespace(name="test_agent")
        # Counts and ordering the tests assert on
        self.events_seen: list[str] = []
        self.user_input_calls: list[tuple[str, float]] = []
        self.controller_calls: list[tuple[str, float]] = []
        # Flag flipped to True while inside _process_event_with_controller
        self._in_controller = False
        self._concurrent_in_controller = False

    async def _process_event_with_controller(
        self, event: TriggerEvent, controller: Any
    ) -> None:
        """Stand-in for the real controller stage.

        Sleeps long enough that any failure of the lock to cover
        pre-controller work would manifest as a second
        ``on_user_input`` arriving while we're still here.
        """
        if self._in_controller:
            self._concurrent_in_controller = True
        self._in_controller = True
        self.controller_calls.append((event.content, asyncio.get_event_loop().time()))
        try:
            await asyncio.sleep(0.05)
        finally:
            self._in_controller = False


class _FakeOutputRouter:
    def __init__(self, agent: _FakeAgent) -> None:
        self._agent = agent

    async def on_user_input(self, content: str) -> None:
        # Detect concurrent pre-controller work: the lock must already
        # be held by the same task that will reach the controller, so
        # if we're called while another _process_event is still in its
        # controller stage, the lock failed.
        if self._agent._in_controller:
            self._agent._concurrent_in_controller = True
        self._agent.user_input_calls.append((content, asyncio.get_event_loop().time()))

    async def emit(self, event) -> None:
        # Mirror BaseOutputModule.emit forwarding for the events the
        # fake cares about (user_input). Anything else is a no-op.
        if event.type == "user_input" and isinstance(event.content, str):
            await self.on_user_input(event.content)


@pytest.mark.asyncio
async def test_process_event_serializes_pre_controller_work():
    """Two concurrent triggers serialize through the full pipeline.

    The fake output router records the timestamp of each
    ``on_user_input`` call, and ``_process_event_with_controller``
    sleeps 50ms.  If pre-controller work were outside the lock, the
    second call's ``on_user_input`` would land within that 50ms window
    while the first call is still in the controller stage.
    """
    agent = _FakeAgent()
    process_event = AgentHandlersMixin._process_event.__get__(agent)

    e1 = TriggerEvent(type="user_input", content="hello-1")
    e2 = TriggerEvent(type="user_input", content="hello-2")

    await asyncio.gather(process_event(e1), process_event(e2))

    # Both pre-controller calls happened
    assert len(agent.user_input_calls) == 2
    # And both controller stages happened
    assert len(agent.controller_calls) == 2
    # Critically: nobody was running the controller stage when the
    # other call's on_user_input fired.
    assert agent._concurrent_in_controller is False

    # Order: the second on_user_input must come AFTER the first
    # controller call started AND completed (because the lock prevents
    # the second pre-work from beginning until the first run finishes).
    first_user, second_user = agent.user_input_calls
    first_ctrl, second_ctrl = agent.controller_calls
    # Pair-by-content: which user_input went first?
    user_order = sorted(agent.user_input_calls, key=lambda x: x[1])
    ctrl_order = sorted(agent.controller_calls, key=lambda x: x[1])
    # The second user_input must occur AFTER the first controller stage
    # finishes.  Timing test: second_user.t >= first_ctrl.t + sleep
    assert user_order[1][1] >= ctrl_order[0][1] + 0.04, (
        f"Second user_input fired during first controller stage — "
        f"pre-controller work raced. user_order={user_order} "
        f"ctrl_order={ctrl_order}"
    )


@pytest.mark.asyncio
async def test_process_event_drops_when_stopped():
    """Lock check still fires if agent stops between trigger and acquire."""
    agent = _FakeAgent()
    agent._running = False

    process_event = AgentHandlersMixin._process_event.__get__(agent)
    await process_event(TriggerEvent(type="user_input", content="x"))

    # No work happened at all — pre-controller side effects must also
    # be gated by ``_running`` (covered now that they're inside the lock).
    assert agent.user_input_calls == []
    assert agent.controller_calls == []
