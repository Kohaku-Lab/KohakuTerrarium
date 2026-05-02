"""Wave B observability wiring for the Agent class.

Routes scratchpad writes and plugin hook timings through the
``output_router`` so SessionOutput can persist them as
``scratchpad_write`` / ``plugin_hook_timing`` events. Factored out of
``core/agent.py`` so that file stays under the 1000-line hard cap.
"""

from typing import Any

from kohakuterrarium.core.metrics_hook import metrics
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


_TOKEN_KEYS = ("prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens")


def init_branch_state(agent: Any) -> None:
    """Initialise per-agent branch lineage + per-turn token accumulator.

    Lives here (instead of inline in ``Agent.__init__``) because the
    agent constructor sits at the file-size hard cap. Side-effecting
    by design: writes ``_turn_index``, ``_branch_id``,
    ``_parent_branch_path``, ``_last_turn_text``, and
    ``_turn_usage_accum`` onto the agent.
    """
    agent._wiring_resolver = None
    agent._turn_index = 0
    agent._branch_id = 0
    agent._parent_branch_path = []
    agent._last_turn_text = []
    agent._turn_usage_accum = dict.fromkeys(_TOKEN_KEYS, 0)


def wire_scratchpad_observer(agent: Any) -> None:
    """Route scratchpad set/delete calls to the agent's output router.

    No-op when session / scratchpad / router is missing.
    """
    session = getattr(agent, "session", None)
    if session is None:
        return
    pad = getattr(session, "scratchpad", None)
    if pad is None or not hasattr(pad, "set_write_observer"):
        return
    agent_name = agent.config.name
    router = getattr(agent, "output_router", None)
    if router is None:
        return

    def _observe(key: str, action: str, size_bytes: int) -> None:
        try:
            router.notify_activity(
                "scratchpad_write",
                f"[{agent_name}] {action} {key}",
                metadata={
                    "agent": agent_name,
                    "key": key,
                    "action": action,
                    "size_bytes": size_bytes,
                },
            )
        except Exception as e:  # pragma: no cover — observability
            logger.debug("scratchpad_write emit failed", error=str(e), exc_info=True)

    pad.set_write_observer(_observe)


def wire_plugin_hook_timing(agent: Any) -> None:
    """Route plugin hook timings to the agent's output router AND
    forward into the process-metrics hook.

    No-op when plugins / callback hook is missing.
    """
    plugins = getattr(agent, "plugins", None)
    if plugins is None or not hasattr(plugins, "set_hook_timing_callback"):
        return
    router = getattr(agent, "output_router", None)
    if router is None:
        return

    def _observe(hook: str, plugin: str, duration_ms: float, blocked: bool) -> None:
        try:
            router.notify_activity(
                "plugin_hook_timing",
                f"[{plugin}] {hook} {duration_ms:.2f}ms",
                metadata={
                    "hook": hook,
                    "plugin": plugin,
                    "duration_ms": duration_ms,
                    "blocked": blocked,
                },
            )
        except Exception as e:  # pragma: no cover — observability
            logger.debug(
                "plugin_hook_timing emit failed",
                error=str(e),
                exc_info=True,
            )
        try:
            metrics.observe_plugin_hook(plugin, hook, duration_ms)
            if blocked:
                metrics.observe_error("plugin")
        except Exception:  # pragma: no cover — observability
            pass

    plugins.set_hook_timing_callback(_observe)


# ─── Wave G session_info helper ───────────────────────────────────────


def build_session_info(agent: Any, tokens_view: str) -> dict[str, Any]:
    """Build the ``session_info`` payload for :meth:`Agent.session_info`.

    Kept in this module so ``core/agent.py`` stays under the 1000-line
    hard cap while exposing the Wave G ``tokens_view`` knob. Returns
    ``{"agent": <name>, "tokens": <own|all_loops payload>}``; when no
    session store is attached the ``tokens`` slot falls back to the
    shape expected by the chosen view (empty dict for ``"own"``, empty
    list for ``"all_loops"``).
    """
    info: dict[str, Any] = {"agent": agent.config.name}
    store = getattr(agent, "session_store", None)
    if store is None:
        info["tokens"] = [] if tokens_view == "all_loops" else {}
        return info
    if tokens_view == "all_loops":
        info["tokens"] = store.token_usage_all_loops()
    else:
        info["tokens"] = store.token_usage(agent.config.name)
    return info
