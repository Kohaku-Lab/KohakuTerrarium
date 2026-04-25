"""Per-agent runtime working-directory controller.

Composition helper attached to :class:`~kohakuterrarium.core.agent.Agent`
as ``agent.workspace``. Switches the agent's effective ``cwd`` mid-session
without rebuilding the agent.

Refresh checklist (kept in lockstep with ``bootstrap/agent_init``):

* ``agent.executor._working_dir`` — every ``ToolContext`` is built from
  this on demand (see ``Executor._build_tool_context``), so updating
  the attribute is enough — no per-tool reset needed.
* ``agent._path_guard`` is rebuilt with the new cwd (and the same
  ``mode``) and re-pointed at ``executor._path_guard``. Without this,
  paths inside the new tree would still trip the warn-once guard.
* ``agent._file_read_state`` is cleared. Read-before-write tracking
  is path-keyed; stale entries from the old tree could mislead
  subsequent ``edit`` / ``write`` calls.
* Sub-agents share the parent's executor reference
  (``subagent_manager._parent_executor``), so their tool contexts
  pick up the new cwd automatically.
* ``agent.session_store.meta["pwd"]`` is updated so resume restores
  the latest cwd, not the initial one.

Constraints:

* Reject when ``agent._processing_task`` is alive — switching mid-turn
  would let some in-flight tools see the old cwd while others see the
  new one. Callers should interrupt first.
* Process-wide ``os.chdir`` is *not* called: the serving manager runs
  many agents in one process; chdir would cross-contaminate cwd for
  every agent.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from kohakuterrarium.utils.file_guard import FileReadState, PathBoundaryGuard
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent

logger = get_logger(__name__)


class WorkspaceController:
    """Switch the agent's tool-side working directory at runtime."""

    def __init__(self, agent: "Agent") -> None:
        self._agent = agent

    # ── Read ────────────────────────────────────────────────────

    def get(self) -> str:
        """Return the agent's current resolved cwd as a string."""
        executor = getattr(self._agent, "executor", None)
        wd = getattr(executor, "_working_dir", None) if executor else None
        if wd is None:
            return str(Path.cwd())
        return str(Path(wd).resolve())

    # ── Mutate ──────────────────────────────────────────────────

    def set(self, new_path: str | Path) -> str:
        """Switch the agent's cwd. Returns the resolved new path string.

        Raises:
            ValueError: ``new_path`` does not exist or is not a directory.
            RuntimeError: the agent is mid-turn (interrupt first).
        """
        if not new_path:
            raise ValueError("Working directory path is required")
        resolved = Path(new_path).expanduser().resolve()
        if not resolved.exists():
            raise ValueError(f"Working directory does not exist: {resolved}")
        if not resolved.is_dir():
            raise ValueError(f"Working directory is not a directory: {resolved}")

        agent = self._agent
        if getattr(agent, "_processing_task", None) is not None:
            raise RuntimeError(
                "Cannot switch working directory while the agent is processing. "
                "Interrupt the current turn first."
            )

        executor = getattr(agent, "executor", None)
        if executor is None:
            raise RuntimeError("Agent has no executor; cannot switch working dir.")

        executor._working_dir = resolved

        # Rebuild path guard with the same mode the agent was created with.
        previous_guard = getattr(agent, "_path_guard", None)
        mode = getattr(previous_guard, "mode", None) or getattr(
            agent.config, "pwd_guard", "warn"
        )
        new_guard = PathBoundaryGuard(cwd=resolved, mode=mode)
        agent._path_guard = new_guard
        executor._path_guard = new_guard

        # Read-before-write tracking is path-keyed — entries under the old
        # tree are misleading after a switch. Replace, don't .clear(), so
        # any tool holding a stale reference doesn't keep mutating it.
        new_state = FileReadState()
        agent._file_read_state = new_state
        executor._file_read_state = new_state

        # Persist via session-store meta. Resume reads ``meta["pwd"]`` and
        # process-cwds into it before reconstructing the agent.
        store = getattr(agent, "session_store", None)
        if store is not None:
            try:
                store.meta["pwd"] = str(resolved)
                touch = getattr(store, "touch", None)
                if callable(touch):
                    touch()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Failed to persist new working_dir to session meta",
                    agent_name=getattr(agent.config, "name", ""),
                    error=str(exc),
                )

        logger.info(
            "agent_working_dir_switched",
            agent_name=getattr(agent.config, "name", ""),
            new_pwd=str(resolved),
        )
        return str(resolved)
