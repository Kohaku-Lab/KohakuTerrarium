"""Ask user tool — request human input mid-execution via the
Phase B output-event bus.

Phase B rewire: emits an ``ask_text`` :class:`OutputEvent` and awaits
the :class:`UIReply` from whichever renderer (TUI / web frontend /
custom) the user is interacting with. Replaces the legacy stderr /
stdin path with a typed, multi-renderer interaction.

Falls back to a stdin read only when no router / agent is available
(programmatic invocation in tests, etc.).
"""

import asyncio
import sys
from typing import Any
from uuid import uuid4

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@register_builtin("ask_user")
class AskUserTool(BaseTool):
    """Request human input mid-execution.

    Phase B: emits an ``ask_text`` OutputEvent through the agent's
    output bus. Renderers (TUI modal, web composer, custom adapters)
    surface the prompt and post the reply back; the awaiting tool
    returns the reply text to the LLM.
    """

    needs_context: bool = True
    # ``ask_user`` and ``show_card`` cover overlapping interaction
    # patterns; force the model to read the manual once so it picks
    # the right tool (free-text vs button choice) and uses the right
    # arg shape — same pattern as ``edit`` / ``multi_edit``.
    require_manual_read: bool = True

    @property
    def tool_name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return "Ask the user a question and wait for response"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        """Ask user via the output-event bus, falling back to stdin."""
        question = args.get("question", "") or args.get("body", "")
        if not question:
            return ToolResult(error="Question is required")

        # Default ``None`` = wait forever. Tools that need a bounded
        # wait can still pass ``timeout_s`` as an argument.
        raw_timeout = args.get("timeout_s")
        timeout_s = float(raw_timeout) if raw_timeout is not None else None
        placeholder = args.get("placeholder", "")
        multiline = bool(args.get("multiline", False))

        agent = getattr(context, "agent", None) if context else None
        router = getattr(agent, "output_router", None) if agent else None

        if router is None:
            # No bus available — legacy stdin fallback for programmatic
            # / test contexts. Mirrors the pre-Phase-B behaviour.
            return await self._stdin_fallback(question)

        event_id = f"ask_{uuid4().hex[:12]}"
        event = OutputEvent(
            type="ask_text",
            interactive=True,
            surface=args.get("surface", "chat"),
            id=event_id,
            timeout_s=timeout_s,
            payload={
                "prompt": question,
                "placeholder": placeholder,
                "multiline": multiline,
            },
        )

        try:
            reply = await router.emit_and_wait(event, timeout_s=timeout_s)
        except Exception as e:
            logger.debug("ask_user bus emit failed", error=str(e), exc_info=True)
            return await self._stdin_fallback(question)

        if reply.is_timeout:
            return ToolResult(output="(no response within timeout)", exit_code=0)

        text = (reply.values or {}).get("text", "")
        if not text:
            return ToolResult(output="(no response)", exit_code=0)
        return ToolResult(output=text, exit_code=0)

    async def _stdin_fallback(self, question: str) -> ToolResult:
        """Pre-Phase-B stdin behaviour for callers without a router."""
        try:
            sys.stderr.write(f"\n[Agent Question] {question}\n> ")
            sys.stderr.flush()
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, sys.stdin.readline)
            response = response.strip()
            if not response:
                return ToolResult(output="(no response)", exit_code=0)
            return ToolResult(output=response, exit_code=0)
        except EOFError:
            return ToolResult(error="No input available (stdin closed)")
        except Exception as e:
            return ToolResult(error=f"Failed to get user input: {e}")
