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
from kohakuterrarium.session.history import select_live_event_ids
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.cli_rich.app import RichCLIApp

logger = get_logger(__name__)


class ScrollbackCommitter:
    """Owns the path from "this should appear in scrollback" to a real
    write to the terminal. Wraps either:

    - ``run_in_terminal(...)`` while the prompt_toolkit Application is
      running (cursor is parked above the app area, our writes go to
      real scrollback, app redraws below).
    - Direct ``sys.stdout`` writes when the Application hasn't started
      yet (e.g. resume replay between ``agent.start()`` and ``app.run``).
    """

    def __init__(self, app: "RichCLIApp"):
        self.app = app
        # Whitespace discipline — track whether the last thing committed
        # to scrollback ended with a blank line. Every "logical block"
        # (user msg, assistant msg, tool panel, sub-agent block) should
        # be surrounded by exactly one blank line before and after.
        # Tracking this avoids double-blanks when two adjacent callers
        # both try to add their own leading blank.
        self._last_was_blank: bool = True  # start-of-scrollback is "blank"
        # Rule-sharing state for tool/sub-agent blocks. When a tool
        # commits via ``block_renderable``, we emit a top rule (opening
        # the block's upper edge) + content, and defer the BOTTOM rule.
        # If the next commit is another tool, that tool's top rule
        # doubles as the previous block's bottom rule — one shared line
        # between two blocks instead of two, saving vertical space.
        # If the next commit is a non-tool (assistant text, user msg,
        # etc.), we emit the deferred closing rule first, then hand off
        # to the normal commit path. Also flushed at explicit turn
        # boundaries via ``flush_block_close``.
        self._pending_block_close: bool = False

    def renderable(self, renderable: Any) -> None:
        self.flush_block_close()
        width = self.app._terminal_width()
        ansi = render_to_ansi(renderable, width)
        # Block-shaped items (Panel, Group, etc.) are visually dense;
        # ensure one blank line before AND after so they don't abut
        # against neighboring blocks.
        self._ensure_leading_blank()
        self.ansi(ansi)
        self.blank_line()

    def block_renderable(self, renderable: Any) -> None:
        """Commit a tool/sub-agent block with shared rule separators.

        Layout::

            ═══════ (OUTER double rule — opens a block sequence)
            content 1 — header hugs the rule, no blank
            ─────── (inner single rule — seam between adjacent blocks)
            content 2
            ─────── (seam)
            content 3
            ═══════ (OUTER double rule — closes the sequence, deferred)

        Rationale for the double/single split:

        * Outer (opening / closing) = DOUBLE — loud, signals "this is
          where a block sequence begins or ends".
        * Inner (seam between adjacent blocks) = SINGLE — quieter, the
          eye reads the sequence as one unit with subtle dividers.

        No blank line between rules and content — the user flagged that
        blanks made tools feel "detached" from their own separators.
        """
        width = self.app._terminal_width()
        content_ansi = render_to_ansi(renderable, width)
        # Rich's Rule renders with its own trailing newline; ``_raw_ansi``
        # also auto-pads a newline when the written string doesn't end
        # in one. That second safety net USED TO double-up after
        # single-line content (header-only tools), producing a stray
        # blank line. We now let ``_raw_ansi`` handle trailing-newline
        # normalisation and don't add any explicit extras here.
        outer_rule_ansi = render_to_ansi(
            Rule(style="bright_black", characters="═"), width
        )
        seam_rule_ansi = render_to_ansi(Rule(style="bright_black"), width)

        if not self._pending_block_close:
            # Starting a fresh block sequence: blank line above, DOUBLE
            # opening rule, content flush against the rule.
            self._ensure_leading_blank()
            self._raw_ansi(outer_rule_ansi)
        else:
            # Continuing a sequence: SINGLE inner seam rule between
            # this block and the previous one.
            self._raw_ansi(seam_rule_ansi)

        self._raw_ansi(content_ansi)
        # NOTE: do NOT explicitly append ``\n`` here. ``_raw_ansi``
        # already normalises a missing trailing newline on its own; a
        # second ``_raw_ansi("\n")`` would produce a blank row after
        # short single-line content (e.g. header-only ``read``/``info``
        # tools where the body policy is 0).
        self._pending_block_close = True
        self._last_was_blank = False

    def flush_block_close(self) -> None:
        """Emit the deferred closing rule if a block sequence is open.

        Closing rule is DOUBLE — matches the opening rule since they're
        both the "outer" boundary of the sequence. The single-line seam
        is reserved for block-to-block transitions.
        """
        if not self._pending_block_close:
            return
        width = self.app._terminal_width()
        rule_ansi = render_to_ansi(Rule(style="bright_black", characters="═"), width)
        self._raw_ansi(rule_ansi)
        self._pending_block_close = False
        self._last_was_blank = False

    def text(self, markup: str) -> None:
        self.flush_block_close()
        width = self.app._terminal_width()
        ansi = render_to_ansi(Text.from_markup(markup), width)
        self._ensure_leading_blank()
        self.ansi(ansi)
        self.blank_line()

    def user_message(self, text: str) -> None:
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
        # Use AssistantMessageBlock.to_committed() so the same Markdown
        # detection + PrefixedRenderable layout is applied as during
        # live streaming. Keeps live and replay visually identical.
        msg = AssistantMessageBlock()
        msg.append(text)
        self.renderable(msg.to_committed())

    def blank_line(self) -> None:
        """Emit a single blank line to scrollback — deduplicated.

        If the previous write already ended on a blank row, this call
        is a no-op. Keeps the "one blank between blocks" rule enforced
        even when multiple helpers each try to add their own bookend.
        """
        # Close any open block sequence first — a blank line is a
        # non-block boundary.
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
        # Reset the blank-line flag based on what we just wrote so the
        # next `blank_line()` call can decide whether it's a no-op.
        self._last_was_blank = ansi.endswith("\n\n")

    def _raw_ansi(self, ansi: str) -> None:
        """Write ANSI bytes to scrollback without touching the blank
        tracker. The public ``ansi()`` + ``blank_line()`` maintain the
        tracker — internal helpers should use this when they're
        emitting a bookend the tracker already accounts for.
        """
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
            # Application not running yet — write directly. Stdout still
            # belongs to the terminal, so the bytes flow into real
            # scrollback as if we'd just printed.
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
    """Replay a list of recorded session events to scrollback.

    Called from ``RichCLIApp`` after ``agent.start()`` but before
    ``app.run_async()`` so the writes land in real terminal scrollback
    above the prompt area.
    """

    def __init__(self, app: "RichCLIApp"):
        self.app = app
        self._committer = app.committer
        # Buffer text chunks across processing_start/end so we can
        # commit one ◆ per turn.
        self._text_buffer: list[str] = []
        self._in_turn = False
        # Sub-agent blocks kept in memory from subagent_call through
        # subagent_result so their children can be attached as they
        # arrive (subagent_tool events fire between call and result).
        self._pending_sa_blocks: dict[str, ToolCallBlock] = {}
        # Direct tool blocks kept in memory from tool_call through
        # tool_result. The saved event stream persists output under
        # ``tool_result`` events, not ``tool_call`` — so to replay a
        # tool with its output we MUST hold the block open until the
        # matching tool_result arrives. Before this pair existed the
        # replay path committed on tool_call with empty output, which
        # meant every resumed tool showed an empty-body panel.
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

        # Drop events on superseded branches (regen / edit+rerun) so
        # the resume scrollback shows only the latest branch per turn.
        live_ids = select_live_event_ids(events)

        for event in events:
            eid = event.get("event_id")
            if isinstance(eid, int) and eid not in live_ids:
                continue
            self._handle_event(event)

        # Trailing flush in case the session ended mid-turn
        self._flush_text_buffer()
        # Any tool_call without a matching tool_result — the agent was
        # interrupted before the tool could report back. Commit with
        # empty output so the block still appears in scrollback.
        for block in list(self._pending_tool_blocks.values()):
            block.set_done("")
            self._committer.block_renderable(block.to_committed())
        self._pending_tool_blocks.clear()
        # Close any open block sequence so the scrollback ends on a
        # proper bottom rule rather than a dangling open block.
        self._committer.flush_block_close()

        scroll.print(Text("--- resume complete ---", style="dim"))
        scroll.print()

    # ── Event dispatch ──

    def _handle_event(self, event: dict) -> None:
        etype = event.get("type", "")
        data = event.get("data", event)

        if etype == "user_input":
            content = data.get("content", "")
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
            # Defer commit until tool_result arrives — otherwise the
            # panel commits with an empty body because the saved event
            # stream keeps output in a separate ``tool_result`` event.
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
                # Orphan result (tool_call missing from the stream) —
                # build a minimal block so the output is still visible.
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
            # Route through block_renderable so the rule separators
            # (top/bottom, shared ═ between consecutive tools) match
            # the live path. Using ``renderable`` here was the bug —
            # replayed tools showed up with no visible separators.
            self._committer.block_renderable(block.to_committed())
            return

        if etype == "subagent_call":
            # Build a ToolCallBlock NOW but hold it in ``_pending_sa_blocks``
            # until the matching subagent_result event. Any subagent_tool
            # events that land in between attach themselves as children.
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
                # Commit the "dispatched in background" notice now, so
                # live and replay produce the same scrollback layout.
                self._committer.renderable(block.build_dispatch_notice())
            self._pending_sa_blocks[job_id] = block
            return

        if etype == "subagent_tool":
            # Attach tool-call children to the pending sub-agent block.
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
                # Find the oldest still-running child matching name.
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
                # Orphan result — build a fresh block (no children) as fallback
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
            # Route through block_renderable to get the rule separators
            # matching the live path.
            self._committer.block_renderable(block.to_committed())
            return

        # Other event types (token_usage, compact_*) are ignored on
        # replay — they're either subsumed by the blocks above or not
        # visually meaningful.

    def _flush_text_buffer(self) -> None:
        if not self._text_buffer:
            return
        text = "".join(self._text_buffer).strip()
        self._text_buffer.clear()
        if not text:
            return
        self._committer.assistant_message(text)


def _format_args(args: dict) -> str:
    """Compact key=value preview, mirroring agent_handlers._notify_tool_start."""
    if not isinstance(args, dict) or not args:
        return ""
    parts = []
    for k, v in args.items():
        if k.startswith("_"):
            continue
        parts.append(f"{k}={str(v)[:40]}")
    return " ".join(parts)[:80]
