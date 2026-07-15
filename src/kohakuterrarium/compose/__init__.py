"""Agent Composition Algebra — Pythonic operators for combining agents.

Usage::

    from kohakuterrarium.compose import agent, factory, Pure

    # Persistent session with conversation carry-over.
    async with await agent("@kt-biome/creatures/swe") as swe:
        result = await (swe >> extract_code >> reviewer)(task)

    # Isolated session per invocation.
    specialist = factory(make_config("coder"))
    result = await specialist("implement this feature")

    # Compose sequence, parallel work, fallback, and retries.
    pipeline = (expert * 2) | generalist
    results = await (analyst & writer & designer)(task)

    # Feed each result back until native control flow stops iteration.
    async for result in (writer >> reviewer).iterate(task):
        if "APPROVED" in result:
            break
"""

from kohakuterrarium.compose.agent import AgentFactory, AgentRunnable, agent, factory
from kohakuterrarium.compose.core import (
    BaseRunnable,
    FailsWhen,
    Fallback,
    PipelineIterator,
    Product,
    Pure,
    Retry,
    Router,
    Runnable,
    Sequence,
    pure,
)

__all__ = [
    "AgentFactory",
    "AgentRunnable",
    "BaseRunnable",
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
    "pure",
]
