"""Session resume for ktui: recorded events drawn through a :class:`CreatureView`.

Only the live branch is shown (after a regenerate or an edit), text of one turn becomes one
assistant message, and tool calls wait for their results so each lands as one sealed cell.
Token usage and the context limit are restored for the footer.
"""

from typing import TYPE_CHECKING, Any

from kohakuterrarium.builtins.ktui.format import args_preview, one_line
from kohakuterrarium.builtins.ktui.tool_bodies import images_of, media_of
from kohakuterrarium.llm.message import content_display_text
from kohakuterrarium.session.history.replay import (
    collect_branch_metadata,
    dedupe_adjacent_duplicate_events,
    select_live_event_ids,
)

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.view import CreatureView


def replay(
    view: "CreatureView",
    events: list[dict],
    branch_view: dict[int, int] | None = None,
    until: tuple[int, bool] | None = None,
    dividers: bool = True,
    last_turns: int | None = None,
) -> None:
    """Draws ``events`` (as stored by the session) into ``view``: the branches of
    ``branch_view`` (the latest of every turn without one). ``until`` = (turn, keep its user
    message) stops at that turn: an edit redraws up to the message it replaces, a retry up to
    and with the message it answers again."""
    if not events:
        return
    events = dedupe_adjacent_duplicate_events(events)
    live = select_live_event_ids(events, branch_view=branch_view)
    view.versions = _versions(events, branch_view)
    state = _Replay(view)
    if dividers:
        view.render("divider", "resumed session")
    events = [
        e
        for e in events
        if not (isinstance(e.get("event_id"), int) and e["event_id"] not in live)
    ]
    # a long history starts with its last turns; the older ones load on demand
    starts = [i for i, e in enumerate(events) if (e.get("type") or "") in USER_KINDS]
    if last_turns is not None and len(starts) > last_turns:
        cut = starts[len(starts) - last_turns]
        for event in events[:cut]:
            if (event.get("type") or "") in ("token_usage", "session_info"):
                state.handle(event)
        view.render("older", len(starts) - last_turns)
        events = events[cut:]
    for event in events:
        turn = event.get("turn_index")
        if until is not None and isinstance(turn, int) and turn >= until[0]:
            kind = event.get("type") or ""
            if not (until[1] and turn == until[0] and kind in USER_KINDS):
                break
        state.handle(event)
    state.finish()
    if dividers:
        view.render("divider", "resume complete")


USER_KINDS = ("user_input", "user_input_injected")
# a resumed (or redrawn) history shows its last turns; the older ones load on demand
RESUME_TURNS = 40


def _versions(
    events: list[dict], branch_view: dict[int, int] | None
) -> dict[int, tuple[int, int]]:
    """turn → (version shown, versions) for the turns with several branches."""
    out: dict[int, tuple[int, int]] = {}
    for turn, info in collect_branch_metadata(events, branch_view=branch_view).items():
        branches = sorted(info.get("branches") or [])
        if len(branches) < 2:
            continue
        shown = (branch_view or {}).get(turn, info.get("latest_branch") or branches[-1])
        index = branches.index(shown) + 1 if shown in branches else len(branches)
        out[turn] = (index, len(branches))
    return out


class _Replay:
    def __init__(self, view: "CreatureView"):
        self.view = view
        self.text: list[str] = []
        # the turn and branch of the text being collected: they key its <ui> blocks' state
        self.turn: tuple[int, int] | None = None
        self.calls: dict[str, tuple[str, str, Any]] = {}
        self.subagents: dict[str, str] = {}

    def flush_text(self) -> None:
        text = "".join(self.text).strip()
        self.text.clear()
        if text:
            self.view.render("reply_turn", self.turn)
            self.view.render("text", text)
            self.view.render("assistant_end")

    def handle(self, event: dict) -> None:
        kind = event.get("type") or event.get("etype") or ""
        data: Any = event.get("data") if isinstance(event.get("data"), dict) else event
        view, usage = self.view, self.view.usage
        match kind:
            case "user_input" | "user_input_injected":
                self.flush_text()
                content = content_display_text(data.get("content", ""))
                turn = event.get("turn_index")
                if content:
                    turn = turn if isinstance(turn, int) else None
                    view.render("user_message", content, None, turn)
                for url, name in images_of(data.get("content")):
                    view.render("image", url, name)
            case "text" | "text_chunk":
                self.text.append(str(data.get("content", "") or ""))
                turn, branch = data.get("turn_index"), data.get("branch_id")
                if isinstance(turn, int) and isinstance(branch, int):
                    self.turn = (turn, branch)
            case "processing_end":
                self.flush_text()
            case "tool_call":
                self.flush_text()
                name = str(data.get("name", ""))
                self.calls[str(data.get("call_id", ""))] = (
                    name,
                    args_preview(name, data.get("args")),
                    data.get("args"),
                )
            case "tool_result" | "deferred_job_result":
                call_id = str(data.get("call_id") or data.get("job_id", ""))
                name, detail, args = self.calls.pop(
                    call_id, (str(data.get("name", "")), "", None)
                )
                context = data.get("context") or {}
                error = data.get("error") or context.get("error")
                exit_code = data.get("exit_code", context.get("exit_code", 0))
                raw = data.get("output", data.get("content", "")) or ""
                parts = raw if isinstance(raw, list) else data.get("result")
                output = (
                    content_display_text(raw) if isinstance(raw, list) else str(raw)
                )
                cancelled = context.get("cancelled") is True
                interrupted = context.get("interrupted") is True
                final_state = context.get("final_state")
                failed = (
                    bool(error)
                    or exit_code not in (0, None)
                    or cancelled
                    or interrupted
                    or final_state in {"cancelled", "interrupted", "failed", "error"}
                )
                message = str(error or output or f"exit {exit_code}") if failed else ""
                view.render("tool_start", call_id, name, detail, False)
                view.render("tool_end", call_id, name, output, message, 0.0, args)
                for media, url, label in media_of(parts):
                    view.render(
                        "image" if media == "image" else "media", url, label, media
                    )
            case "subagent_call":
                self.flush_text()
                self.subagents[str(data.get("job_id", ""))] = one_line(
                    data.get("task", ""), 70
                )
            case "subagent_result":
                job_id = str(data.get("job_id", ""))
                name = str(data.get("name", ""))
                view.render(
                    "subagent_start",
                    job_id,
                    name,
                    self.subagents.pop(job_id, ""),
                    False,
                )
                turns = data.get("turns", 0)
                stats = f"{turns} turns" if turns else "done"
                error = str(data.get("error") or "")
                view.render(
                    "subagent_end",
                    job_id,
                    name,
                    str(data.get("output", "") or ""),
                    error,
                    stats,
                )
            case "token_usage":
                prompt = int(data.get("prompt_tokens", 0) or 0)
                usage.tokens_in += prompt
                usage.tokens_out += int(data.get("completion_tokens", 0) or 0)
                usage.cached += int(data.get("cached_tokens", 0) or 0)
                if prompt:
                    usage.last_prompt = prompt
            case "session_info":
                usage.max_context = int(data.get("max_context", 0) or usage.max_context)
                usage.model = data.get("llm_name") or data.get("model") or usage.model
            case "compact_complete":
                self.flush_text()
                view.render(
                    "compact_end",
                    int(data.get("round", 0) or 0),
                    str(data.get("summary", "") or ""),
                    "",
                )

    def finish(self) -> None:
        self.flush_text()
        for call_id, (name, detail, _) in self.calls.items():
            view = self.view
            view.render("tool_start", call_id, name, detail, False)
            view.render("tool_end", call_id, name, "", "no result recorded", 0.0)
        self.calls.clear()
