"""
Termination conditions for agent execution.

Configurable conditions that stop the agent loop: max turns, max tokens,
max duration, idle timeout, keyword detection, and pluggable checkers
contributed by plugins.
"""

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
    """Vote returned by a plugin termination checker.

    Any-can-stop: if any plugin returns ``should_stop=True``, the run
    terminates and the plugin's reason string is surfaced in
    ``session_info`` metadata as ``terminated_by_reason``.
    """

    should_stop: bool
    reason: str = ""


@dataclass
class TerminationContext:
    """Snapshot of run state passed to plugin termination checkers.

    Contains enough state for a checker to decide without reaching back
    into the agent. Built-in conditions (max_turns, max_duration, …)
    still run first; plugin checkers fire only when no built-in has
    already terminated the run.
    """

    turn_count: int
    elapsed: float
    idle_time: float
    last_output: str
    scratchpad: "Scratchpad | None" = None
    recent_tool_results: list[Any] = field(default_factory=list)


@dataclass
class TerminationConfig:
    """
    Configuration for termination conditions.

    All conditions are optional. If multiple are set, ANY triggered condition stops the agent.
    """

    max_turns: int = 0  # Max controller turns (0 = unlimited)
    max_tokens: int = 0  # Total token budget (0 = unlimited) - reserved for future
    max_duration: float = 0  # Max duration in seconds (0 = unlimited)
    idle_timeout: float = 0  # No events for N seconds (0 = unlimited)
    keywords: list[str] = field(default_factory=list)  # Stop on output keyword


class TerminationChecker:
    """
    Checks termination conditions during agent execution.

    Usage:
        checker = TerminationChecker(config)
        checker.start()

        # In event loop:
        checker.record_turn()
        checker.record_activity()

        if checker.should_terminate(last_output="TASK_COMPLETE"):
            break
    """

    def __init__(self, config: TerminationConfig):
        self.config = config
        self._turn_count: int = 0
        self._start_time: float = 0.0
        self._last_activity: float = 0.0
        self._terminated: bool = False
        self._reason: str = ""
        # Plugin-supplied checkers, wired by Agent._init_plugins.
        self._plugin_manager: "PluginManager | None" = None
        # Hooks to pull state for TerminationContext without coupling
        # this module to Agent. Populated by Agent wiring.
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
        """Start tracking. Call at beginning of agent run."""
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
        """
        Check if any termination condition is met.

        Built-in conditions fire first (max_turns, max_duration,
        idle_timeout, keywords). If none fire, plugin-supplied checkers
        vote — any returning ``TerminationDecision(should_stop=True,
        …)`` wins (any-can-stop, per cluster 3.3).

        Args:
            last_output: The last output text from the controller
                (for keyword check and plugin context)

        Returns:
            True if agent should terminate
        """
        if self._terminated:
            return True

        now = time.monotonic()

        # Check max turns
        if self.config.max_turns > 0 and self._turn_count >= self.config.max_turns:
            self._terminated = True
            self._reason = f"Max turns reached ({self._turn_count})"
            logger.info("Termination: %s", self._reason)
            return True

        # Check max duration
        if self.config.max_duration > 0:
            elapsed = now - self._start_time
            if elapsed >= self.config.max_duration:
                self._terminated = True
                self._reason = f"Max duration reached ({elapsed:.1f}s)"
                logger.info("Termination: %s", self._reason)
                return True

        # Check idle timeout
        if self.config.idle_timeout > 0:
            idle_time = now - self._last_activity
            if idle_time >= self.config.idle_timeout:
                self._terminated = True
                self._reason = f"Idle timeout ({idle_time:.1f}s)"
                logger.info("Termination: %s", self._reason)
                return True

        # Check keywords in output
        if self.config.keywords and last_output:
            for keyword in self.config.keywords:
                if keyword in last_output:
                    self._terminated = True
                    self._reason = f"Keyword detected: {keyword}"
                    logger.info("Termination: %s", self._reason)
                    return True

        # Plugin-supplied checkers (any-can-stop).
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
        """Poll plugin-supplied checkers. Returns first positive vote."""
        manager = self._plugin_manager
        if manager is None:
            return None
        try:
            checkers: list[tuple[str, Callable[[Any], Any]]] = (
                manager.collect_termination_checkers()
            )
        except Exception as e:  # pragma: no cover — defensive
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
        """Force the agent into a terminated state with ``reason``.

        Used when an external signal (e.g., ``BudgetExhausted``) needs
        to end the run cleanly without going through the standard
        built-in/plugin checker chain.
        """
        self._terminated = True
        self._reason = reason

    @property
    def reason(self) -> str:
        """Get termination reason (empty if not terminated)."""
        return self._reason

    @property
    def turn_count(self) -> int:
        """Get current turn count."""
        return self._turn_count

    @property
    def elapsed(self) -> float:
        """Get elapsed time since start."""
        if self._start_time == 0:
            return 0.0
        return time.monotonic() - self._start_time

    @property
    def is_active(self) -> bool:
        """Check if any termination condition is configured.

        Plugin-supplied checkers count too — a checker that fires even
        without built-in conditions should still keep the termination
        subsystem active.
        """
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
            except Exception:  # pragma: no cover — defensive
                return False
        return False
