"""Controller-side metrics helpers.

Split from :mod:`core.controller` to keep the controller file under
the 1000-line hard cap while still owning the LLM-call timing /
identity-resolution glue.

These helpers are imported and called from inside the controller's two
streaming loops; nothing else in the codebase should reach for them.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any

from kohakuterrarium.core.metrics_hook import metrics


class _LLMCallTimer:
    """Tracks status across the streaming call, observed in ``__exit__``.

    Returned from :func:`time_llm_call` so the controller can flag
    the call ``"interrupted"`` from inside the loop without raising.
    """

    __slots__ = ("status",)

    def __init__(self) -> None:
        self.status = "ok"


@contextmanager
def time_llm_call(llm: Any):
    """Bracket an LLM streaming call with timing + status metrics.

    Usage:

        with time_llm_call(self.llm) as t:
            async for chunk in self.llm.chat(...):
                if self._interrupted:
                    t.status = "interrupted"
                    break
                ...

    On exception, status flips to ``"error"`` and we increment
    ``errors_total{controller}`` before re-raising. ``observe_llm``
    always fires from the ``finally`` branch so a crashed call still
    contributes to provider latency stats (we just label it as such).

    Yields the :class:`_LLMCallTimer` so the caller can read
    ``provider`` / ``model`` (filled at enter time) and override the
    status field before the exit observation.
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
    """Best-effort ``(provider, model)`` for metrics labels.

    Providers expose ``provider_name`` (canonical short name like
    ``codex`` / ``openai`` / ``anthropic``) and either ``model`` or
    ``config.model``. Empty fallbacks produce a ``"unknown"`` label
    rather than blowing up the metrics emit.
    """
    provider = getattr(llm, "provider_name", "") or ""
    model = (
        getattr(llm, "model", "")
        or getattr(getattr(llm, "config", None), "model", "")
        or ""
    )
    return provider or "unknown", model or "unknown"


def emit_token_metrics(llm: Any, provider: str, model: str) -> None:
    """Forward the LLM's last_usage (post-turn) into the metrics hook.

    Mirrors what ``_log_token_usage`` already records on the
    ``output_router`` activity log; we duplicate the read here rather
    than hook into the activity bus because the activity bus has no
    structured token fields — every consumer parses metadata dicts.
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
    except Exception:  # pragma: no cover — defensive
        pass
