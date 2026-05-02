"""Process-wide metrics observation hook.

Thin pub/sub between the agent's hot path (controller / tool dispatch /
sub-agent loop / plugin manager) and one or more aggregator backends.
Core ONLY emits — it does not maintain counters, histograms, or any
storage. The default aggregator (``serving.process_metrics``) is the
canonical subscriber and registers itself on import; tests and plugins
can attach additional subscribers via :meth:`MetricsHook.subscribe`.

Why a hook (and not a direct call into the aggregator) — keeping core
free of aggregator dependencies means the aggregator can move,
restructure, or be replaced (Prometheus exporter, OpenTelemetry, ...)
without touching the dozens of emit sites scattered across
``controller.py`` / ``agent_tools.py`` / ``agent_handlers.py`` /
``modules/subagent/base.py``.

Subscribers must NEVER raise: every observe_* call wraps each
subscriber callback in ``try/except`` and logs failures at debug level.
The hot path cannot afford a metrics bug crashing a turn.
"""

from __future__ import annotations

from typing import Any, Protocol

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class MetricsSubscriber(Protocol):
    """Subscriber surface — implement only the events you care about.

    Every method has a no-op default so partial implementations are
    fine. The aggregator implements all of them.
    """

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
    """Process-wide metrics fan-out.

    Use the module-level :data:`metrics` singleton — never instantiate
    in production code. Tests construct a fresh one and swap it in via
    :func:`_set_singleton_for_tests`.
    """

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
        """Drop all subscribers. Tests only."""
        self._subscribers.clear()

    # ── observe_* — every emit site funnels through one of these. ──

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

    # ── private ──

    def _fanout(self, method: str, *args: Any, **kwargs: Any) -> None:
        for sub in list(self._subscribers):
            fn = getattr(sub, method, None)
            if fn is None:
                continue
            try:
                fn(*args, **kwargs)
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug(
                    "Metrics subscriber failed",
                    method=method,
                    error=str(exc),
                    exc_info=True,
                )


# Process-wide singleton. Import this everywhere; do NOT construct your
# own. The default aggregator subscribes itself on first import of
# ``serving.process_metrics``.
metrics = MetricsHook()


def _set_singleton_for_tests(new: MetricsHook) -> MetricsHook:
    """Swap the singleton for the duration of a test. Returns the
    previous instance so the test fixture can restore it."""
    global metrics
    previous = metrics
    metrics = new
    return previous
