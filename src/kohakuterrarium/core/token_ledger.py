"""Per-agent running token totals, current context fill, and context window.

The ledger is the live source for the ``token_usage`` / ``context_tokens``
fields carried by creature status, ``session_info`` frames and ``token_usage``
activity frames. ``context_window`` is the single source for the
``max_context`` / ``compact_threshold`` pair those payloads report.
"""

from typing import Any

TOKEN_FIELDS = ("prompt", "completion", "cached", "total")

_PERSISTED_KEYS = (
    ("prompt", "total_input_tokens", "prompt_tokens"),
    ("completion", "total_output_tokens", "completion_tokens"),
    ("cached", "total_cached_tokens", "cached_tokens"),
)


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def empty_token_usage() -> dict[str, int]:
    """Return the zero-valued ``token_usage`` shape."""
    return dict.fromkeys(TOKEN_FIELDS, 0)


class TokenLedger:
    """Running token totals and last prompt size for one agent.

    Totals extend the agent's persisted ``<name>:token_usage`` slot. Each time
    a different store is seen the ledger takes the field-wise maximum of its
    own and the persisted totals, so resume, store merges and splits keep
    lifetime figures. ``context_tokens`` is the prompt size of the most recent
    LLM call and drops to zero when the conversation is compacted or cleared.
    ``total`` is always ``prompt + completion``.
    """

    def __init__(self) -> None:
        self.prompt = 0
        self.completion = 0
        self.cached = 0
        self.context_tokens = 0
        self._context_known = False
        self._seeded_store: Any = None

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    def seed(self, store: Any, namespace: str) -> None:
        """Merge persisted totals from ``store`` once per distinct store."""
        if store is None or store is self._seeded_store:
            return
        self._seeded_store = store
        try:
            raw = store.state.get(f"{namespace}:token_usage")
        except Exception:
            raw = None
        if not isinstance(raw, dict):
            return
        for field, persisted_key, event_key in _PERSISTED_KEYS:
            persisted = _as_int(raw.get(persisted_key, raw.get(event_key)))
            setattr(self, field, max(getattr(self, field), persisted))
        if not self._context_known and "last_prompt_tokens" in raw:
            self.context_tokens = _as_int(raw.get("last_prompt_tokens"))
            self._context_known = True

    def add(self, usage: dict[str, Any]) -> None:
        """Account one LLM call's usage and adopt its prompt as context fill."""
        prompt = _as_int(usage.get("prompt_tokens"))
        self.prompt += prompt
        self.completion += _as_int(usage.get("completion_tokens"))
        self.cached += _as_int(usage.get("cached_tokens"))
        self.context_tokens = prompt
        self._context_known = True

    def reset_context(self) -> None:
        """Mark the conversation as rewritten: no prompt has been measured since."""
        self.context_tokens = 0
        self._context_known = True

    def totals(self) -> dict[str, int]:
        """Return the public ``token_usage`` shape."""
        return {
            "prompt": self.prompt,
            "completion": self.completion,
            "cached": self.cached,
            "total": self.total,
        }


def ledger_for(agent: Any) -> TokenLedger:
    """Return ``agent``'s ledger, seeded from its current session store."""
    ledger = getattr(agent, "_token_ledger", None)
    if not isinstance(ledger, TokenLedger):
        ledger = TokenLedger()
        try:
            agent._token_ledger = ledger
        except AttributeError:
            pass
    config = getattr(agent, "config", None)
    namespace = getattr(config, "name", "")
    if isinstance(namespace, str) and namespace:
        ledger.seed(getattr(agent, "session_store", None), namespace)
    return ledger


def token_snapshot(agent: Any) -> dict[str, Any]:
    """Return ``{"token_usage": {...}, "context_tokens": n}`` for ``agent``."""
    if agent is None:
        return {"token_usage": empty_token_usage(), "context_tokens": 0}
    ledger = ledger_for(agent)
    return {"token_usage": ledger.totals(), "context_tokens": ledger.context_tokens}


def reset_context_tokens(agent: Any) -> None:
    """Zero ``agent``'s context fill after its conversation was rewritten."""
    if agent is not None:
        ledger_for(agent).reset_context()


def model_context_window(agent: Any) -> int | None:
    """The preset's known window size for ``agent``'s chat model, or ``None``."""
    llm = getattr(agent, "llm", None)
    value = getattr(llm, "_profile_context_window", None)
    return value if type(value) is int and value > 0 else None


def context_window(agent: Any) -> tuple[int, int]:
    """Return ``(max_context, compact_threshold)`` for ``agent``.

    ``max_context`` is the chat model's window, falling back to the compaction
    window. ``compact_threshold`` is the prompt size at which auto-compaction
    actually fires (compaction window x threshold), or 0 without compaction.
    """
    cfg = getattr(getattr(agent, "compact_manager", None), "config", None)
    compact_max = _as_int(getattr(cfg, "max_tokens", 0))
    llm = getattr(agent, "llm", None)
    max_context = _as_int(getattr(llm, "_profile_max_context", 0)) or compact_max
    threshold = getattr(cfg, "threshold", 0)
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        threshold = 0
    compact_at = int(compact_max * threshold) if compact_max and threshold else 0
    return max_context, compact_at
