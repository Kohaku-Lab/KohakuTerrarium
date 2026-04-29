"""Branch command — list or switch the live branch of a turn.

Different from ``/fork`` (which copies the session into a new file):
``/branch`` rewires which sibling branch of an existing turn the
agent treats as live. Useful for CLI/TUI users who otherwise have no
way to navigate the ``<1/N>`` alternatives created by ``/regen`` or
``/edit``.

Usage::

    /branch                    — list every turn that has alternatives
    /branch <turn> <id>        — switch turn ``turn`` to branch ``id``
    /branch latest             — reset every turn to its latest branch
"""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)
from kohakuterrarium.session.history import (
    collect_branch_metadata,
    collect_user_groups,
    dedupe_adjacent_duplicate_events,
    replay_conversation,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _format_listing(meta: dict[int, dict], user_groups: dict[int, dict]) -> str:
    """Pretty-print every turn that has alternatives, splitting the
    listing into user-side edits and assistant-side regens so the
    user sees which level the alternatives live at."""
    if not meta:
        return "No branches recorded yet."
    lines: list[str] = []
    has_any = False
    for ti in sorted(meta.keys()):
        info = meta[ti]
        groups = (user_groups.get(ti) or {}).get("groups") or []
        edit_count = len(groups)
        regen_max = max((len(g["branches"]) for g in groups), default=0)
        if edit_count <= 1 and regen_max <= 1:
            continue
        has_any = True
        if edit_count > 1:
            for gi, group in enumerate(groups, start=1):
                preview = (group["content"] or "").replace("\n", " ")[:40]
                lines.append(
                    f"  turn {ti} edit {gi}/{edit_count}: branches "
                    f"{group['branches']} — {preview!r}"
                )
        else:
            branches = info["branches"]
            lines.append(
                f"  turn {ti} regen 1..{len(branches)}: branches {branches} "
                f"(latest: {info['latest_branch']})"
            )
    if not has_any:
        return "No multi-branch turns yet — every turn has a single live response."
    return (
        "Turns with alternatives:\n"
        + "\n".join(lines)
        + ("\n\nUse '/branch <turn> <branch_id>' to switch.")
    )


@register_user_command("branch")
class BranchCommand(BaseUserCommand):
    name = "branch"
    aliases = ["br"]
    description = "List or switch the live branch of a turn (regen / edit alternatives)"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        agent = context.agent
        if not agent or agent.session_store is None:
            return UserCommandResult(error="No agent / session store in context.")
        events = dedupe_adjacent_duplicate_events(
            agent.session_store.get_events(agent.config.name)
        )
        meta = collect_branch_metadata(events)
        user_groups = collect_user_groups(events)

        tokens = (args or "").split()
        if not tokens:
            return UserCommandResult(output=_format_listing(meta, user_groups))

        if tokens[0] == "latest":
            # Drop any branch_view override on the agent so replay sees
            # every turn at its latest branch.
            agent._branch_view = {}
            replayed = replay_conversation(events)
            agent.controller.conversation = _rebuild_conv(
                replayed, agent.controller.conversation.__class__
            )
            return UserCommandResult(
                output=f"Switched every turn back to its latest branch ({len(replayed)} messages)."
            )

        if len(tokens) < 2:
            return UserCommandResult(
                error="Usage: /branch <turn_index> <branch_id>  |  /branch latest  |  /branch"
            )
        try:
            turn_index = int(tokens[0])
            branch_id = int(tokens[1])
        except ValueError:
            return UserCommandResult(error="turn_index and branch_id must be integers.")

        info = meta.get(turn_index)
        if not info:
            return UserCommandResult(error=f"Turn {turn_index} has no recorded events.")
        if branch_id not in info["branches"]:
            return UserCommandResult(
                error=(
                    f"Turn {turn_index} has no branch {branch_id}. "
                    f"Available: {info['branches']}"
                )
            )

        # Persist the override on the agent. ``replay_conversation``
        # picks it up; the agent's runtime conversation is rebuilt to
        # match so the next LLM turn sees the chosen branch's history.
        view = dict(getattr(agent, "_branch_view", {}) or {})
        view[turn_index] = branch_id
        agent._branch_view = view

        replayed = replay_conversation(events, branch_view=view)
        agent.controller.conversation = _rebuild_conv(
            replayed, agent.controller.conversation.__class__
        )
        return UserCommandResult(
            output=(
                f"Switched turn {turn_index} → branch {branch_id} "
                f"({len(replayed)} messages live)."
            )
        )


def _rebuild_conv(messages: list[dict], conv_cls):
    """Reconstruct a ``Conversation`` from replay output."""
    conv = conv_cls()
    for msg in messages:
        kwargs = {}
        if msg.get("tool_calls"):
            kwargs["tool_calls"] = msg["tool_calls"]
        if msg.get("tool_call_id"):
            kwargs["tool_call_id"] = msg["tool_call_id"]
        if msg.get("name"):
            kwargs["name"] = msg["name"]
        conv.append(msg.get("role", "user"), msg.get("content", ""), **kwargs)
    return conv
