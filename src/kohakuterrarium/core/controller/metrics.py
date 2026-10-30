"""Metrics instrumentation for controller LLM calls."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any

from kohakuterrarium.core.metrics_hook import metrics


class _LLMCallTimer:
    """Carry mutable completion status across a streaming call."""

    __slots__ = ("status",)

    def __init__(self) -> None:
        self.status = "ok"


@contextmanager
def time_llm_call(llm: Any):
    """Measure an LLM stream and emit status, latency, error, and token metrics.

    The yielded timer lets callers mark interruption without raising. Exceptions
    are recorded as errors and re-raised, while latency is observed in all cases.
    """
    provider, model = llm_identity(llm)
    t0 = time.monotonic()
    timer = _LLMCallTimer()
    try:
        yield timer
    except Exception:
        timer.status = "error"
        metrics.observe_error("controller")
        raise
    finally:
        metrics.observe_llm(
            provider, model, timer.status, (time.monotonic() - t0) * 1000.0
        )
        emit_token_metrics(llm, provider, model)


def llm_identity(llm: Any) -> tuple[str, str]:
    """Return stable provider and model labels, falling back to ``unknown``."""
    provider = getattr(llm, "provider_name", "") or ""
    model = (
        getattr(llm, "model", "")
        or getattr(getattr(llm, "config", None), "model", "")
        or ""
    )
    return provider or "unknown", model or "unknown"


def emit_token_metrics(llm: Any, provider: str, model: str) -> None:
    """Emit structured token counts from the provider's latest usage data.

    Usage is read directly because activity events expose token data only through
    untyped metadata, which would make metric extraction consumer-dependent.
    """
    usage = getattr(llm, "last_usage", None) or getattr(llm, "_last_usage", None)
    if not usage:
        return
    try:
        metrics.observe_tokens(
            provider,
            model,
            prompt=int(usage.get("prompt_tokens", 0) or 0),
            completion=int(usage.get("completion_tokens", 0) or 0),
            cache_read=int(
                usage.get("cached_tokens", 0)
                or usage.get("cache_read_input_tokens", 0)
                or 0
            ),
            cache_write=int(usage.get("cache_creation_input_tokens", 0) or 0),
        )
    except Exception:  # pragma: no cover - metrics must not fail the turn
        pass
