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
from rich.text import Text

from kohakuterrarium.builtins.cli_rich.blocks.message import AssistantMessageBlock
from kohakuterrarium.builtins.cli_rich.blocks.tool import ToolCallBlock
from kohakuterrarium.builtins.cli_rich.live_region import render_to_ansi
from kohakuterrarium.builtins.cli_rich.theme import (
    COLOR_USER,
    ICON_USER,
)
from kohakuterrarium.builtins.cli_rich.runtime import spawn
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

    def renderable(self, renderable: Any) -> None:
        width = self.app._terminal_width()
        ansi = render_to_ansi(renderable, width)
        self.ansi(ansi)

    def text(self, markup: str) -> None:
        width = self.app._terminal_width()
        ansi = render_to_ansi(Text.from_markup(markup), width)
        self.ansi(ansi)

    def user_message(self, text: str) -> None:
        body = Text()
        body.append(f"{ICON_USER} ", style=COLOR_USER)
        body.append(text)
        width = self.app._terminal_width()
        ansi = render_to_ansi(body, width)
        self.ansi(ansi)

    def assistant_message(self, text: str) -> None:
        # Use AssistantMessageBlock.to_committed() so the same Markdown
        # detection + PrefixedRenderable layout is applied as during
        # live streaming. Keeps live and replay visually identical.
        msg = AssistantMessageBlock()
        msg.append(text)
        self.renderable(msg.to_committed())

    def blank_line(self) -> None:
        self.ansi("\n")

    def ansi(self, ansi: str) -> None:
        if not ansi:
            return

        def _emit() -> None:
            try:
                sys.stdout.write(ansi)
                if not ansi.endswith("\n"):
                    sys.stdout.write("\n")
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

        for event in events:
            self._handle_event(event)

        # Trailing flush in case the session ended mid-turn
        self._flush_text_buffer()

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

        if etype == "text":
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
            self._flush_text_buffer()
            block = ToolCallBlock(
                job_id=data.get("call_id", ""),
                name=data.get("name", ""),
                args_preview=_format_args(data.get("args", {})),
                kind="tool",
            )
            block.set_done("")  # mark done so it renders ✓
            self._committer.renderable(block.to_committed())
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
            self._committer.renderable(block.to_committed())
            return

        # Other event types (tool_result, token_usage, compact_*) are
        # ignored on replay — they're either covered by tool_call /
        # subagent_call above, or not visually meaningful.

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
