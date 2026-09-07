"""Scrollback commit helpers + session-event replay for the rich CLI.

These run inside ``app.run_in_terminal`` while the prompt_toolkit
Application is alive (so the cursor moves above the app area first),
or directly to stdout when the app hasn't started yet (e.g. during
the resume replay before ``app.run_async``).
"""

import sys
from typing import TYPE_CHECKING, Any

from prompt_toolkit.application import run_in_terminal
from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from kohakuterrarium.builtins.cli_rich.blocks.message import AssistantMessageBlock
from kohakuterrarium.builtins.cli_rich.blocks.tool import ToolCallBlock
from kohakuterrarium.builtins.cli_rich.live_region import render_to_ansi
from kohakuterrarium.builtins.cli_rich.runtime import spawn
from kohakuterrarium.builtins.cli_rich.theme import COLOR_USER, ICON_USER
from kohakuterrarium.builtins.outputs.stdout import _write_safe
from kohakuterrarium.llm.message import content_display_text
from kohakuterrarium.session.history import (
    dedupe_adjacent_duplicate_events,
    select_live_event_ids,
)
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.cli_rich.app import RichCLIApp

logger = get_logger(__name__)


class ScrollbackCommitter:
    """Commit Rich output to terminal scrollback without corrupting the live area."""

    def __init__(self, app: "RichCLIApp"):
        self.app = app
        # Logical blocks are separated by exactly one blank line.
        self._last_was_blank: bool = True
        # Defer closing rules so adjacent tool blocks can share one separator.
        self._pending_block_close: bool = False
        # Capture method calls per creature for focused-log redraws.
        self._capture_target: str | None = None
        self._captures: dict[str, list[tuple[str, tuple]]] = {}
        self._replaying: bool = False

    def set_capture_target(self, creature_id: str | None) -> None:
        """Capture subsequent commits under a creature, or disable capture."""
        self._capture_target = creature_id
        if creature_id is not None:
            self._captures.setdefault(creature_id, [])

    def captured_for(self, creature_id: str) -> list[tuple[str, tuple]]:
        return list(self._captures.get(creature_id, []))

    def clear_capture(self, creature_id: str) -> None:
        self._captures.pop(creature_id, None)

    def set_replay_mode(self, replaying: bool) -> None:
        """While True, commits do NOT get appended to capture buckets."""
        self._replaying = replaying

    def _record(self, method: str, args: tuple) -> None:
        if self._replaying or self._capture_target is None:
            return
        self._captures.setdefault(self._capture_target, []).append((method, args))

    def renderable(self, renderable: Any) -> None:
        self._record("renderable", (renderable,))
        self.flush_block_close()
        width = self.app._terminal_width()
        ansi = render_to_ansi(renderable, width)
        # Dense renderables need whitespace boundaries from neighboring blocks.
        self._ensure_leading_blank()
        self.ansi(ansi)
        self.blank_line()

    def block_renderable(self, renderable: Any) -> None:
        """Commit a tool block with double outer rules and shared inner seams."""
        self._record("block_renderable", (renderable,))
        width = self.app._terminal_width()
        content_ansi = render_to_ansi(renderable, width)
        # _raw_ansi owns trailing-newline normalization for rules and content.
        outer_rule_ansi = render_to_ansi(
            Rule(style="bright_black", characters="═"), width
        )
        seam_rule_ansi = render_to_ansi(Rule(style="bright_black"), width)

        if not self._pending_block_close:
            self._ensure_leading_blank()
            self._raw_ansi(outer_rule_ansi)
        else:
            self._raw_ansi(seam_rule_ansi)

        self._raw_ansi(content_ansi)
        # An extra newline here would create a blank row for single-line blocks.
        self._pending_block_close = True
        self._last_was_blank = False

    def flush_block_close(self) -> None:
        """Close an open tool-block sequence with its double outer rule."""
        if not self._pending_block_close:
            return
        width = self.app._terminal_width()
        rule_ansi = render_to_ansi(Rule(style="bright_black", characters="═"), width)
        self._raw_ansi(rule_ansi)
        self._pending_block_close = False
        self._last_was_blank = False

    def text(self, markup: str) -> None:
        self._record("text", (markup,))
        self.flush_block_close()
        width = self.app._terminal_width()
        ansi = render_to_ansi(Text.from_markup(markup), width)
        self._ensure_leading_blank()
        self.ansi(ansi)
        self.blank_line()

    def user_message(self, text: str) -> None:
        self._record("user_message", (text,))
        self.flush_block_close()
        body = Text()
        body.append(f"{ICON_USER} ", style=COLOR_USER)
        body.append(text)
        width = self.app._terminal_width()
        ansi = render_to_ansi(body, width)
        self._ensure_leading_blank()
        self.ansi(ansi)
        self.blank_line()

    def assistant_message(self, text: str) -> None:
        # Reuse live rendering so replayed Markdown has identical layout.
        msg = AssistantMessageBlock()
        msg.append(text)
        self.renderable(msg.to_committed())

    def blank_line(self) -> None:
        """Emit a single blank line to scrollback — deduplicated.

        If the previous write already ended on a blank row, this call
        is a no-op. Keeps the "one blank between blocks" rule enforced
        even when multiple helpers each try to add their own bookend.
        """
        self.flush_block_close()
        if self._last_was_blank:
            return
        self._raw_ansi("\n")
        self._last_was_blank = True

    def _ensure_leading_blank(self) -> None:
        """Emit a blank line if the previous commit didn't leave one."""
        if not self._last_was_blank:
            self._raw_ansi("\n")
            self._last_was_blank = True

    def ansi(self, ansi: str) -> None:
        if not ansi:
            return
        self._raw_ansi(ansi)
        self._last_was_blank = ansi.endswith("\n\n")

    def _raw_ansi(self, ansi: str) -> None:
        """Write ANSI without mutating the public blank-line tracker."""
        if not ansi:
            return

        def _emit() -> None:
            try:
                _write_safe(sys.stdout, ansi)
                if not ansi.endswith("\n"):
                    _write_safe(sys.stdout, "\n")
                sys.stdout.flush()
            except Exception as e:
                logger.exception("scrollback write failed", error=str(e))

        if self.app.app is None:
            # Before Application startup, stdout already maps to real scrollback.
            _emit()
            return
        try:
            spawn(self._run_in_terminal(_emit))
        except Exception as e:
            logger.exception("commit failed", error=str(e))

    async def _run_in_terminal(self, fn) -> None:
        if self.app.app is None:
            try:
                fn()
            except Exception as e:
                logger.exception("scrollback emit failed", error=str(e))
            return
        try:
            await run_in_terminal(fn, in_executor=False)
        except Exception as e:
            logger.exception("run_in_terminal failed", error=str(e))


class SessionReplay:
    """Replay recorded session events into terminal scrollback."""

    def __init__(self, app: "RichCLIApp"):
        self.app = app
        self._committer = app.committer
        # Buffer chunks so each replayed turn commits one assistant block.
        self._text_buffer: list[str] = []
        self._in_turn = False
        # Hold parent blocks until results arrive so child tools can attach.
        self._pending_sa_blocks: dict[str, ToolCallBlock] = {}
        # Tool output lives in tool_result events, so calls remain pending until then.
        self._pending_tool_blocks: dict[str, ToolCallBlock] = {}

    def replay(self, events: list[dict]) -> None:
        if not events:
            return
        scroll = Console(
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
            soft_wrap=False,
            emoji=False,
        )
        scroll.print(Text("--- resumed session history ---", style="dim"))
        scroll.print()

        events = dedupe_adjacent_duplicate_events(events)
        # Resume displays only the live branch after regeneration or edit-and-rerun.
        live_ids = select_live_event_ids(events)

        for event in events:
            eid = event.get("event_id")
            if isinstance(eid, int) and eid not in live_ids:
                continue
            self._handle_event(event)

        self._flush_text_buffer()
        # Interrupted calls without results remain visible with an empty body.
        for block in list(self._pending_tool_blocks.values()):
            block.set_done("")
            self._committer.block_renderable(block.to_committed())
        self._pending_tool_blocks.clear()
        self._committer.flush_block_close()

        scroll.print(Text("--- resume complete ---", style="dim"))
        scroll.print()

    def _handle_event(self, event: dict) -> None:
        etype = event.get("type", "")
        data = event.get("data", event)

        if etype == "user_input":
            # Web sessions persist multimodal content-part lists; Rich Text
            # only accepts str, so render display text first.
            content = content_display_text(data.get("content", ""))
            if content:
                self._committer.user_message(content)
                self._committer.blank_line()
            return

        if etype == "processing_start":
            self._committer.blank_line()
            self._in_turn = True
            self._text_buffer.clear()
            return

        if etype in ("text", "text_chunk"):
            content = data.get("content", "")
            if content:
                self._text_buffer.append(content)
            return

        if etype == "processing_end":
            self._flush_text_buffer()
            self._committer.blank_line()
            self._in_turn = False
            return

        if etype == "tool_call":
            # Defer commit because output is stored in the matching tool_result.
            self._flush_text_buffer()
            call_id = data.get("call_id", "")
            block = ToolCallBlock(
                job_id=call_id,
                name=data.get("name", ""),
                args_preview=_format_args(data.get("args", {})),
                kind="tool",
            )
            self._pending_tool_blocks[call_id] = block
            return

        if etype == "tool_result":
            call_id = data.get("call_id", "")
            block = self._pending_tool_blocks.pop(call_id, None)
            if block is None:
                # Preserve orphaned results even when the call event is absent.
                block = ToolCallBlock(
                    job_id=call_id,
                    name=data.get("name", ""),
                    args_preview="",
                    kind="tool",
                )
            output = str(data.get("output", ""))
            error = data.get("error")
            exit_code = data.get("exit_code", 0)
            if error or exit_code not in (0, None):
                block.set_error(str(error or output or f"exit {exit_code}"))
            else:
                block.set_done(output)
            # Match live tool separator behavior during replay.
            self._committer.block_renderable(block.to_committed())
            return

        if etype == "subagent_call":
            # Hold the parent until its result so intervening child events can attach.
            self._flush_text_buffer()
            job_id = data.get("job_id", "")
            task_text = str(data.get("task", ""))[:200]
            is_bg = bool(data.get("background", False))
            block = ToolCallBlock(
                job_id=job_id,
                name=data.get("name", ""),
                args_preview=task_text,
                kind="subagent",
            )
            if is_bg:
                block.promote_to_background()
                self._committer.renderable(block.build_dispatch_notice())
            self._pending_sa_blocks[job_id] = block
            return

        if etype == "subagent_tool":
            parent_id = data.get("job_id", "")
            parent = self._pending_sa_blocks.get(parent_id)
            if parent is None:
                return
            activity = data.get("activity", "")
            tool_name = data.get("tool_name", "")
            detail = data.get("detail", "")
            if activity == "tool_start":
                child = ToolCallBlock(
                    job_id=f"{parent_id}::sub::{tool_name}::{len(parent.children)}",
                    name=tool_name,
                    args_preview=detail,
                    kind="tool",
                    parent_job_id=parent_id,
                )
                parent.add_child(child)
            elif activity in ("tool_done", "tool_error"):
                for child in parent.children:
                    if child.name == tool_name and child.status == "running":
                        if activity == "tool_error":
                            child.set_error(detail)
                        else:
                            child.set_done(detail)
                        break
            return

        if etype == "subagent_result":
            self._flush_text_buffer()
            job_id = data.get("job_id", "")
            block = self._pending_sa_blocks.pop(job_id, None)
            if block is None:
                # Preserve orphaned sub-agent results without child history.
                block = ToolCallBlock(
                    job_id=job_id,
                    name=data.get("name", ""),
                    args_preview="",
                    kind="subagent",
                )
            if data.get("error"):
                block.set_error(str(data.get("error", "")))
            else:
                block.set_done(
                    str(data.get("output", "")),
                    tools_used=data.get("tools_used", []),
                    turns=data.get("turns", 0),
                    total_tokens=data.get("total_tokens", 0),
                    prompt_tokens=data.get("prompt_tokens", 0),
                    completion_tokens=data.get("completion_tokens", 0),
                )
            self._committer.block_renderable(block.to_committed())
            return

    def _flush_text_buffer(self) -> None:
        if not self._text_buffer:
            return
        text = "".join(self._text_buffer).strip()
        self._text_buffer.clear()
        if not text:
            return
        self._committer.assistant_message(text)


def _format_args(args: dict) -> str:
    """Format a compact key-value argument preview."""
    if not isinstance(args, dict) or not args:
        return ""
    parts = []
    for k, v in args.items():
        if k.startswith("_"):
            continue
        parts.append(f"{k}={str(v)[:40]}")
    return " ".join(parts)[:80]
