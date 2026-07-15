"""Built-in and plugin-defined conditions for ending agent execution."""

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.scratchpad import Scratchpad
    from kohakuterrarium.modules.plugin.manager import PluginManager

logger = get_logger(__name__)


@dataclass(frozen=True)
class TerminationDecision:
    """Represent a plugin's vote to stop and its user-visible reason."""

    should_stop: bool
    reason: str = ""


@dataclass
class TerminationContext:
    """Provide plugin checkers a detached snapshot of relevant run state."""

    turn_count: int
    elapsed: float
    idle_time: float
    last_output: str
    scratchpad: "Scratchpad | None" = None
    recent_tool_results: list[Any] = field(default_factory=list)


@dataclass
class TerminationConfig:
    """Configure optional conditions where any match terminates the run."""

    max_turns: int = 0
    max_tokens: int = 0
    max_duration: float = 0
    idle_timeout: float = 0
    keywords: list[str] = field(default_factory=list)


class TerminationChecker:
    """Track run progress and evaluate configured termination conditions."""

    def __init__(self, config: TerminationConfig):
        self.config = config
        self._turn_count: int = 0
        self._start_time: float = 0.0
        self._last_activity: float = 0.0
        self._terminated: bool = False
        self._reason: str = ""
        self._plugin_manager: "PluginManager | None" = None
        # Agent-supplied references keep checker evaluation independent of Agent.
        self._scratchpad_ref: "Scratchpad | None" = None
        self._recent_tool_results: list[Any] = []

    def attach_plugins(self, manager: "PluginManager | None") -> None:
        """Wire a PluginManager so plugin-supplied checkers vote."""
        self._plugin_manager = manager

    def attach_scratchpad(self, scratchpad: "Scratchpad | None") -> None:
        """Supply the scratchpad reference passed to plugin checkers."""
        self._scratchpad_ref = scratchpad

    def record_tool_result(self, result: Any) -> None:
        """Record a completed tool result (kept as a short tail)."""
        self._recent_tool_results.append(result)
        if len(self._recent_tool_results) > 16:
            self._recent_tool_results = self._recent_tool_results[-16:]

    def start(self) -> None:
        """Reset counters and begin timing a new agent run."""
        self._start_time = time.monotonic()
        self._last_activity = self._start_time
        self._turn_count = 0
        self._terminated = False
        self._reason = ""
        self._recent_tool_results = []
        logger.debug("Termination checker started", config=str(self.config))

    def record_turn(self) -> None:
        """Record a controller turn."""
        self._turn_count += 1
        self._last_activity = time.monotonic()

    def record_activity(self) -> None:
        """Record any activity (resets idle timer)."""
        self._last_activity = time.monotonic()

    def should_terminate(self, last_output: str = "") -> bool:
        """Return whether a built-in condition or plugin vote stops the run.

        Built-ins take precedence; plugins vote only when no built-in condition has
        already terminated execution.
        """
        if self._terminated:
            return True

        now = time.monotonic()

        if self.config.max_turns > 0 and self._turn_count >= self.config.max_turns:
            self._terminated = True
            self._reason = f"Max turns reached ({self._turn_count})"
            logger.info("Termination: %s", self._reason)
            return True

        if self.config.max_duration > 0:
            elapsed = now - self._start_time
            if elapsed >= self.config.max_duration:
                self._terminated = True
                self._reason = f"Max duration reached ({elapsed:.1f}s)"
                logger.info("Termination: %s", self._reason)
                return True

        if self.config.idle_timeout > 0:
            idle_time = now - self._last_activity
            if idle_time >= self.config.idle_timeout:
                self._terminated = True
                self._reason = f"Idle timeout ({idle_time:.1f}s)"
                logger.info("Termination: %s", self._reason)
                return True

        if self.config.keywords and last_output:
            for keyword in self.config.keywords:
                if keyword in last_output:
                    self._terminated = True
                    self._reason = f"Keyword detected: {keyword}"
                    logger.info("Termination: %s", self._reason)
                    return True

        if self._plugin_manager is not None:
            decision = self._run_plugin_checkers(last_output, now)
            if decision is not None and decision.should_stop:
                reason = decision.reason or "Plugin vetoed continuation"
                self._terminated = True
                self._reason = reason
                logger.info("Termination: %s", self._reason)
                return True

        return False

    def _run_plugin_checkers(
        self, last_output: str, now: float
    ) -> TerminationDecision | None:
        """Return the first positive plugin termination vote."""
        manager = self._plugin_manager
        if manager is None:
            return None
        try:
            checkers: list[tuple[str, Callable[[Any], Any]]] = (
                manager.collect_termination_checkers()
            )
        except Exception as e:  # pragma: no cover - plugin failure cannot stop run
            logger.warning(
                "Failed to collect plugin termination checkers",
                error=str(e),
                exc_info=True,
            )
            return None
        if not checkers:
            return None

        ctx = TerminationContext(
            turn_count=self._turn_count,
            elapsed=now - self._start_time if self._start_time else 0.0,
            idle_time=now - self._last_activity if self._last_activity else 0.0,
            last_output=last_output,
            scratchpad=self._scratchpad_ref,
            recent_tool_results=list(self._recent_tool_results),
        )
        for name, fn in checkers:
            try:
                decision = fn(ctx)
            except Exception as e:
                logger.warning(
                    "Plugin termination checker raised",
                    plugin_name=name,
                    error=str(e),
                    exc_info=True,
                )
                continue
            if decision is None:
                continue
            if not isinstance(decision, TerminationDecision):
                logger.warning(
                    "Plugin termination checker returned non-TerminationDecision",
                    plugin_name=name,
                    returned_type=type(decision).__name__,
                )
                continue
            if decision.should_stop:
                return decision
        return None

    def force_terminate(self, reason: str) -> None:
        """Terminate immediately with an externally supplied reason."""
        self._terminated = True
        self._reason = reason

    @property
    def reason(self) -> str:
        """Return the termination reason, or an empty string while active."""
        return self._reason

    @property
    def turn_count(self) -> int:
        """Return the number of recorded controller turns."""
        return self._turn_count

    @property
    def elapsed(self) -> float:
        """Return elapsed seconds since tracking started."""
        if self._start_time == 0:
            return 0.0
        return time.monotonic() - self._start_time

    @property
    def is_active(self) -> bool:
        """Return whether any built-in or plugin condition is configured."""
        c = self.config
        if (
            c.max_turns
            or c.max_tokens
            or c.max_duration
            or c.idle_timeout
            or c.keywords
        ):
            return True
        if self._plugin_manager is not None:
            try:
                return bool(self._plugin_manager.collect_termination_checkers())
            except Exception:  # pragma: no cover - broken plugins count as inactive
                return False
        return False
