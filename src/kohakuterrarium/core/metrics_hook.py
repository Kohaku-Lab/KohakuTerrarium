"""Process-wide fan-out from runtime metric emitters to aggregators.

Core retains no metric state and depends on no aggregation backend. Subscriber
failures are isolated because telemetry must never interrupt an agent turn.
"""

from __future__ import annotations

from typing import Any, Protocol

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class MetricsSubscriber(Protocol):
    """Structural interface for subscribers that implement relevant events."""

    def observe_llm(
        self,
        provider: str,
        model: str,
        status: str,
        duration_ms: float,
        agent: str | None = None,
    ) -> None: ...

    def observe_tokens(
        self,
        provider: str,
        model: str,
        prompt: int,
        completion: int,
        cache_read: int,
        cache_write: int,
        agent: str | None = None,
    ) -> None: ...

    def observe_tool(
        self,
        tool: str,
        status: str,
        duration_ms: float,
        agent: str | None = None,
    ) -> None: ...

    def observe_subagent(
        self,
        name: str,
        status: str,
        duration_ms: float,
        agent: str | None = None,
    ) -> None: ...

    def observe_error(self, source: str, agent: str | None = None) -> None: ...

    def observe_plugin_hook(
        self,
        plugin: str,
        hook: str,
        duration_ms: float,
        agent: str | None = None,
    ) -> None: ...


class MetricsHook:
    """Fan metric observations out to isolated process-wide subscribers."""

    def __init__(self) -> None:
        self._subscribers: list[MetricsSubscriber] = []

    def subscribe(self, subscriber: MetricsSubscriber) -> None:
        if subscriber not in self._subscribers:
            self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: MetricsSubscriber) -> None:
        try:
            self._subscribers.remove(subscriber)
        except ValueError:
            pass

    def reset(self) -> None:
        """Remove all subscribers for test isolation."""
        self._subscribers.clear()

    def observe_llm(
        self,
        provider: str,
        model: str,
        status: str,
        duration_ms: float,
        agent: str | None = None,
    ) -> None:
        self._fanout("observe_llm", provider, model, status, duration_ms, agent=agent)

    def observe_tokens(
        self,
        provider: str,
        model: str,
        prompt: int = 0,
        completion: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
        agent: str | None = None,
    ) -> None:
        self._fanout(
            "observe_tokens",
            provider,
            model,
            prompt,
            completion,
            cache_read,
            cache_write,
            agent=agent,
        )

    def observe_tool(
        self,
        tool: str,
        status: str,
        duration_ms: float,
        agent: str | None = None,
    ) -> None:
        self._fanout("observe_tool", tool, status, duration_ms, agent=agent)

    def observe_subagent(
        self,
        name: str,
        status: str,
        duration_ms: float,
        agent: str | None = None,
    ) -> None:
        self._fanout("observe_subagent", name, status, duration_ms, agent=agent)

    def observe_error(self, source: str, agent: str | None = None) -> None:
        self._fanout("observe_error", source, agent=agent)

    def observe_plugin_hook(
        self,
        plugin: str,
        hook: str,
        duration_ms: float,
        agent: str | None = None,
    ) -> None:
        self._fanout("observe_plugin_hook", plugin, hook, duration_ms, agent=agent)

    def _fanout(self, method: str, *args: Any, **kwargs: Any) -> None:
        for sub in list(self._subscribers):
            fn = getattr(sub, method, None)
            if fn is None:
                continue
            try:
                fn(*args, **kwargs)
            except Exception as exc:  # pragma: no cover - metrics cannot fail turns
                logger.debug(
                    "Metrics subscriber failed",
                    method=method,
                    error=str(exc),
                    exc_info=True,
                )


# Runtime emitters share one hook so subscribers observe a coherent process view.
metrics = MetricsHook()


def _set_singleton_for_tests(new: MetricsHook) -> MetricsHook:
    """Replace the singleton for a test and return the previous hook."""
    global metrics
    previous = metrics
    metrics = new
    return previous
