"""Agent Composition Algebra — Pythonic operators for combining agents.

Usage::

    from kohakuterrarium.compose import agent, factory, Pure

    # Persistent agent (reused across calls)
    async with await agent("@kt-biome/creatures/swe") as swe:
        result = await (swe >> extract_code >> reviewer)(task)

    # Ephemeral agent (fresh per call)
    specialist = factory(make_config("coder"))
    result = await specialist("implement this feature")

    # Operators: >> (sequence), & (parallel), | (fallback), * (retry)
    pipeline = (expert * 2) | generalist
    results = await (analyst & writer & designer)(task)

    # Iterate (loop with native control flow)
    async for result in (writer >> reviewer).iterate(task):
        if "APPROVED" in result:
            break
"""

from kohakuterrarium.compose.agent import AgentFactory, AgentRunnable, agent, factory
from kohakuterrarium.compose.core import (
    BaseRunnable,
    Fallback,
    FailsWhen,
    PipelineIterator,
    Product,
    Pure,
    Retry,
    Router,
    Runnable,
    Sequence,
)
from kohakuterrarium.compose.effects import Effects

__all__ = [
    "AgentFactory",
    "AgentRunnable",
    "BaseRunnable",
    "Effects",
    "Fallback",
    "FailsWhen",
    "PipelineIterator",
    "Product",
    "Pure",
    "Retry",
    "Router",
    "Runnable",
    "Sequence",
    "agent",
    "factory",
]
