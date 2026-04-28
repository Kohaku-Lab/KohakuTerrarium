"""Fork command — branch the current session into a new file.

Different from regen / edit+rerun: those open a new ``branch_id`` of
an existing turn within the same session. ``/fork`` creates a whole
new ``.kohakutr.v2`` file via ``SessionStore.fork()`` (Wave E) so the
user can explore an alternate trajectory without affecting the
current session. The new file is reported back; the user can resume
it later with ``kt resume <name>``.

Usage::

    /fork                       — fork at the current end-of-stream
    /fork <event_id>            — fork at a specific event_id
    /fork --name <new_name>     — name the new session
"""

import uuid
from pathlib import Path

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)
from kohakuterrarium.session.migrations import path_for_version, FORMAT_VERSION
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _parse_args(args: str) -> tuple[int | None, str | None]:
    """Parse ``[event_id] [--name <new_name>]`` into ``(event_id, name)``.

    Returns ``(None, None)`` for fork-at-end with auto-generated name.
    """
    tokens = (args or "").split()
    event_id: int | None = None
    name: str | None = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--name" and i + 1 < len(tokens):
            name = tokens[i + 1]
            i += 2
            continue
        try:
            event_id = int(tok)
        except ValueError:
            pass
        i += 1
    return event_id, name


def _suggest_target_path(parent_path: str, name: str | None) -> str:
    """Resolve the new session file path next to the parent.

    Uses ``path_for_version`` so the suffix matches whatever
    ``MAX_SUPPORTED_VERSION`` is on this build.
    """
    parent = Path(parent_path)
    # Strip ``.v2``-style version suffix to get the bare name.
    stem = parent.name
    for sep in (".v",):
        if sep in stem:
            stem = stem.split(sep)[0] + ".kohakutr"
            break
    base_dir = parent.parent
    fork_name = name or f"fork-{uuid.uuid4().hex[:8]}"
    base = stem.replace(".kohakutr", f"-{fork_name}.kohakutr")
    return path_for_version(str(base_dir / base), FORMAT_VERSION)


@register_user_command("fork")
class ForkCommand(BaseUserCommand):
    name = "fork"
    # No "branch" alias here — ``/branch`` is now a separate command
    # for switching the live regen / edit branch within the same
    # session. Aliasing fork to "branch" would shadow that resolution.
    aliases: list[str] = []
    description = "Fork the current session into a new file (Wave E branching)"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        agent = context.agent
        if not agent or agent.session_store is None:
            return UserCommandResult(error="No agent / session store in context.")
        event_id, name = _parse_args(args)
        store = agent.session_store
        if event_id is None:
            events = store.get_events(agent.config.name)
            event_id = max(
                (
                    e.get("event_id", 0)
                    for e in events
                    if isinstance(e.get("event_id"), int)
                ),
                default=0,
            )
            if event_id <= 0:
                return UserCommandResult(error="No events to fork from.")
        target_path = _suggest_target_path(store.path, name)
        try:
            child = store.fork(target_path, at_event_id=event_id, name=name)
        except Exception as e:
            logger.warning("Fork failed", error=str(e), exc_info=True)
            return UserCommandResult(error=f"Fork failed: {e}")
        child_path = child.path
        child.close(update_status=False)
        return UserCommandResult(
            output=(
                f"Forked at event {event_id} → {child_path}\n"
                f"Resume with: kt resume {child_path}"
            )
        )
