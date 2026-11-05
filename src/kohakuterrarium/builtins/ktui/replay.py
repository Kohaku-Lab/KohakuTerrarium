"""Session resume for ktui: recorded events drawn through a :class:`CreatureView`.

Only the live branch is shown (after a regenerate or an edit), text of one turn becomes one
assistant message, and tool calls wait for their results so each lands as one sealed cell.
Token usage and the context limit are restored for the footer.
"""

from typing import TYPE_CHECKING, Any

from kohakuterrarium.builtins.ktui.format import args_preview, one_line
from kohakuterrarium.llm.message import content_display_text
from kohakuterrarium.session.history.replay import (
    dedupe_adjacent_duplicate_events,
    select_live_event_ids,
)

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.view import CreatureView


def replay(view: "CreatureView", events: list[dict]) -> None:
    """Draws ``events`` (as stored by the session) into ``view``."""
    if not events:
        return
    events = dedupe_adjacent_duplicate_events(events)
    live = select_live_event_ids(events)
    state = _Replay(view)
    view.render("divider", "resumed session")
    for event in events:
        event_id = event.get("event_id")
        if isinstance(event_id, int) and event_id not in live:
            continue
        state.handle(event)
    state.finish()
    view.render("divider", "resume complete")


class _Replay:
    def __init__(self, view: "CreatureView"):
        self.view = view
        self.text: list[str] = []
        # the turn and branch of the text being collected: they key its <ui> blocks' state
        self.turn: tuple[int, int] | None = None
        self.calls: dict[str, tuple[str, str]] = {}
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
                if content:
                    view.render("user_message", content, None)
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
                )
            case "tool_result":
                call_id = str(data.get("call_id", ""))
                name, detail = self.calls.pop(call_id, (str(data.get("name", "")), ""))
                error = data.get("error")
                exit_code = data.get("exit_code", 0)
                output = str(data.get("output", "") or "")
                failed = error or exit_code not in (0, None)
                message = str(error or output or f"exit {exit_code}") if failed else ""
                view.render("tool_start", call_id, name, detail, False)
                view.render("tool_end", call_id, name, output, message, 0.0)
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
        for call_id, (name, detail) in self.calls.items():
            view = self.view
            view.render("tool_start", call_id, name, detail, False)
            view.render("tool_end", call_id, name, "", "no result recorded", 0.0)
        self.calls.clear()
