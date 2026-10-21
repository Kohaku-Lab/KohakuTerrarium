"""List or switch the live response branch within the current session."""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.core.agent_message_history import apply_prepared_branch_view
from kohakuterrarium.core.agent_raw_history import prepare_branch_view_candidate
from kohakuterrarium.core.branch_continuation import (
    persist_branch_view,
    require_no_running_jobs,
    selected_branch_view,
)
from kohakuterrarium.core.history_candidate import require_history_candidate
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
)
from kohakuterrarium.session.snapshot import save_agent_snapshot


def _format_listing(
    meta: dict[int, dict], user_groups: dict[int, dict], selected: dict[int, int]
) -> str:
    """Format turns with alternatives, distinguishing edits from regenerations."""
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
                    f"{group['branches']} — {preview!r} "
                    f"(selected: {selected.get(ti, 'none')})"
                )
        else:
            branches = info["branches"]
            lines.append(
                f"  turn {ti} regen 1..{len(branches)}: branches {branches} "
                f"(selected: {selected.get(ti, 'none')})"
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
        tokens = (args or "").split()
        if not tokens:
            events = dedupe_adjacent_duplicate_events(
                agent.session_store.get_events(agent.config.name)
            )
            return UserCommandResult(
                output=_format_listing(
                    collect_branch_metadata(events),
                    collect_user_groups(events),
                    selected_branch_view(agent),
                )
            )

        latest = tokens == ["latest"]
        view = {}
        if not latest:
            if len(tokens) != 2:
                return UserCommandResult(
                    error="Usage: /branch <turn_index> <branch_id>  |  /branch latest  |  /branch"
                )
            try:
                turn_index, branch_id = map(int, tokens)
            except ValueError:
                return UserCommandResult(
                    error="turn_index and branch_id must be integers."
                )
            view = {turn_index: branch_id}

        async with agent._get_message_mutation_lock():
            await agent._wait_for_history_finalization()
            agent._ensure_history_mutation_idle()
            require_no_running_jobs(agent)
            session_output = getattr(agent, "_session_output", None)
            if session_output is not None:
                await session_output.drain()
            agent._ensure_history_mutation_idle()
            require_no_running_jobs(agent)

            candidate = prepare_branch_view_candidate(agent, view)
            conversation = candidate["conversation"]
            require_history_candidate(agent, conversation.get_messages())
            prepared = candidate["prepared"]
            persist_branch_view(
                agent.session_store, agent.config.name, prepared["selected"]
            )
            apply_prepared_branch_view(agent, prepared, candidate=conversation)
            try:
                save_agent_snapshot(
                    agent.session_store,
                    agent.config.name,
                    agent,
                    agent.controller.conversation.snapshot_messages(),
                )
            except Exception as exc:
                return UserCommandResult(
                    error=f"Branch switched, but saving its checkpoint failed: {exc}"
                )
            count = sum(
                message.role != "system"
                for message in agent.controller.conversation.get_messages()
            )
        if latest:
            return UserCommandResult(
                output=f"Switched to the latest compatible branches ({count} messages live)."
            )
        return UserCommandResult(
            output=f"Switched turn {turn_index} → branch {branch_id} ({count} messages live)."
        )
