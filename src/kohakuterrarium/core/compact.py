"""
Non-blocking auto-compact system.

Automatically summarizes old conversation context in the background
when approaching token limits. The agent keeps working during compaction.

Design:
  - Two-zone model: compact zone (old, will be summarized) + live zone (recent, untouched)
  - Background task: LLM summarizes the compact zone asynchronously
  - Atomic splice: when summary is ready, replaces compact zone
  - Incremental: each round's summary includes the previous summary
  - Emergency truncation: if summarization fails, truncate oldest messages
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from kohakuterrarium.llm.message import create_message
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Defaults for auto-compaction.
# max_tokens is the model's context window size. Set this to match
# the model you are using (e.g. 272000 for gpt-5.4, 1000000 for Gemini 3).
# threshold triggers compaction when prompt_tokens reaches this fraction
# of max_tokens. If context somehow exceeds max_tokens, emergency truncation.
DEFAULT_MAX_TOKENS = 256_000
DEFAULT_THRESHOLD = 0.80  # compact when prompt_tokens >= 80% of max_tokens
DEFAULT_TARGET = 0.40  # aim for 40% of max_tokens after compact
DEFAULT_KEEP_RECENT = 8  # keep last 8 turns raw (not summarized)

COMPACT_PROMPT = """You are summarizing a conversation between an AI agent and a user (or between agents in a team).

Create a structured summary with these exact sections:

### Current Goal
What is the agent currently trying to achieve?

### Key Decisions
What important choices were made, and why? Include approximate position (early/mid/late in conversation).

### Progress
List completed, in-progress, and pending tasks. Use [DONE], [IN PROGRESS], [PENDING] markers.

### Files Modified
List files that were read, written, or edited with brief context.

### Key Facts
Important details that the agent needs to remember to continue working.

### Keywords
Comma-separated list of important terms for keyword search.

### Key Sentences
Exact quotes from the conversation that should be preserved verbatim.

Rules:
- Preserve decision rationale ("X because Y", not just "decided X")
- Keep exact file paths, line numbers, error codes verbatim
- Use relative temporal markers ("early in session", "after fixing X")
- Do NOT include raw tool output (it's searchable in session history)
- Focus on what the agent needs to CONTINUE working, not a narrative
- If there is a previous summary included, merge its information with new content
"""


@dataclass
class CompactConfig:
    """Configuration for auto-compaction."""

    max_tokens: int = DEFAULT_MAX_TOKENS
    threshold: float = DEFAULT_THRESHOLD
    target: float = DEFAULT_TARGET
    keep_recent_turns: int = DEFAULT_KEEP_RECENT
    enabled: bool = True
    # If set, use a different model for summarization (cheaper/faster)
    compact_model: str | None = None


class CompactManager:
    """Manages non-blocking context compaction.

    Attached to an agent. Checks after each LLM call whether compaction
    is needed, and runs it in the background if so.
    """

    def __init__(self, config: CompactConfig | None = None):
        self.config = config or CompactConfig()
        self._compacting = False
        self._compact_task: asyncio.Task | None = None
        self._last_compact_time: float = 0
        self._compact_count: int = 0
        # References set by agent
        self._controller: Any = None
        self._llm: Any = None
        self._session_store: Any = None
        self._output_router: Any = None
        self._agent_name: str = ""
        # Plugin manager (set by agent via _init_compact_manager when
        # plugins are wired up). Used for on_compact_start veto + the
        # on_compact_end notification. ``None`` = no-op.
        self._plugins: Any = None

    @property
    def is_compacting(self) -> bool:
        return self._compacting

    def should_compact(self, prompt_tokens: int = 0) -> bool:
        """Check if compaction should be triggered.

        Uses prompt_tokens from the last LLM call. Token-based only,
        no character estimation fallback.
        """
        if not self.config.enabled or self._compacting:
            return False

        if prompt_tokens > 0:
            return prompt_tokens >= self.config.max_tokens * self.config.threshold

        return False

    def trigger_compact(self) -> None:
        """Start compaction as a background task.

        Emits ``compact_start`` up-front, then runs background
        summarization, then ``compact_complete`` (or ``compact_skipped``
        if the conversation is too short to benefit).

        Pre-check short-conversation case BEFORE flipping ``_compacting``
        or emitting ``compact_start``. Otherwise the UI gets stuck on a
        "compacting..." banner because the background task returns via
        the early-out in ``_run_compact`` without emitting a completion
        event. Terrariums hit this path often — the root agent's
        conversation is typically just orchestration so ``/compact`` on
        root has "nothing to compact".
        """
        if self._compacting or not self._controller:
            return

        # Pre-check size. The same logic is replicated inside _run_compact
        # for the early-return path; we check here too so we can emit a
        # clean ``compact_skipped`` without flipping the compacting flag
        # or misleadingly showing a "compacting..." banner.
        messages = self._controller.conversation.get_messages()
        keep_count = self._count_keep_messages(messages)
        boundary = len(messages) - keep_count
        if boundary <= 1:
            if self._output_router:
                self._output_router.notify_activity(
                    "compact_skipped",
                    "Not enough context to compact — nothing to summarize",
                    metadata={
                        "reason": "too_short",
                        "round": self._compact_count + 1,
                    },
                )
                # Wave B additive ``compact_decision`` — records that
                # the manager saw the pre-check and chose to skip.
                self._output_router.notify_activity(
                    "compact_decision",
                    f"[{self._agent_name}] skipped (too_short)",
                    metadata={
                        "reason": "too_short",
                        "tokens_before": 0,
                        "tokens_after": 0,
                        "skipped": True,
                    },
                )
            logger.info(
                "Compact skipped — conversation too short",
                agent=self._agent_name,
                message_count=len(messages),
                boundary=boundary,
            )
            return

        self._compacting = True

        # Emit compact_start immediately (before background task)
        if self._output_router:
            self._output_router.notify_activity(
                "compact_start",
                f"Compacting context (round {self._compact_count + 1})",
                metadata={"round": self._compact_count + 1},
            )
            # Wave B ``compact_decision`` — triggered path.
            self._output_router.notify_activity(
                "compact_decision",
                f"[{self._agent_name}] triggered",
                metadata={
                    "reason": "threshold",
                    "tokens_before": 0,
                    "tokens_after": 0,
                    "skipped": False,
                },
            )

        self._compact_task = asyncio.create_task(self._run_compact())
        logger.info(
            "Auto-compact triggered",
            agent=self._agent_name,
            compact_count=self._compact_count + 1,
        )

    async def _run_compact(self) -> None:
        """Background compaction task."""
        try:
            conversation = self._controller.conversation
            messages = conversation.get_messages()

            # Find boundary: keep system prompt + last N turns
            # A "turn" is roughly: user message + assistant response + tool calls
            keep_count = self._count_keep_messages(messages)
            boundary = len(messages) - keep_count

            if boundary <= 1:
                logger.debug("Not enough messages to compact")
                return

            # Compact zone: messages[1:boundary] (skip system at index 0)
            # Live zone: messages[boundary:]
            compact_messages = messages[1:boundary]

            if not compact_messages:
                return

            # Plugin veto: ``on_compact_start`` is a vetoable callback.
            # Any plugin returning ``False`` skips this compaction cycle
            # (e.g. a plugin that just injected critical context).
            # ``on_compact_end`` will NOT fire in this case.
            context_length = conversation.get_context_length()
            if self._plugins is not None:
                proceed = await self._plugins.should_proceed(
                    "on_compact_start",
                    context_length=context_length,
                )
                if not proceed:
                    if self._output_router:
                        self._output_router.notify_activity(
                            "compact_skipped",
                            "Compaction vetoed by plugin",
                            metadata={
                                "reason": "plugin_veto",
                                "round": self._compact_count + 1,
                            },
                        )
                    logger.info(
                        "Compact vetoed by plugin(s)",
                        agent=self._agent_name,
                    )
                    return

            # Build the text to summarize
            summary_input = self._format_messages_for_summary(compact_messages)

            # Call LLM to summarize
            summary = await self._summarize(summary_input)

            if not summary:
                logger.warning(
                    "Compact summarization failed — aborting to preserve context"
                )
                if self._output_router:
                    self._output_router.notify_activity(
                        "processing_error",
                        "[CompactError] Summarization failed — context preserved unchanged",
                        metadata={
                            "error_type": "CompactError",
                            "error": "Summarization LLM call failed. Context was NOT modified.",
                        },
                    )
                return

            # Atomic splice: replace compact zone with summary
            self._splice_conversation(conversation, boundary, summary)

            self._compact_count += 1
            self._last_compact_time = time.time()

            # Notify output for TUI/frontend display
            if self._output_router:
                self._output_router.notify_activity(
                    "compact_complete",
                    f"Context auto-compact done (round {self._compact_count})",
                    metadata={
                        "round": self._compact_count,
                        "summary": summary,
                        "messages_compacted": boundary - 1,
                    },
                )

            # Save conversation snapshot with post-compact version
            # (The compact_complete event is already recorded by SessionOutput
            # via notify_activity above — no need to append_event separately.)
            if self._session_store:
                # Overwrite conversation snapshot with post-compact version
                # so resume gets the compacted conversation, not the full one
                try:
                    self._session_store.save_conversation(
                        self._agent_name,
                        conversation.to_messages(),
                    )
                except Exception as e:
                    logger.debug(
                        "Failed to save compacted conversation",
                        error=str(e),
                        exc_info=True,
                    )

                # Persist compact_count for resume continuity
                try:
                    self._session_store.save_state(
                        self._agent_name,
                        compact_count=self._compact_count,
                    )
                except Exception as e:
                    logger.debug(
                        "Failed to save compact_count state",
                        error=str(e),
                        exc_info=True,
                    )

            logger.info(
                "Auto-compact complete",
                agent=self._agent_name,
                messages_compacted=boundary - 1,
                compact_round=self._compact_count,
            )

            # Fire on_compact_end callback on all plugins (observation
            # only — veto applies pre-compact only).
            if self._plugins is not None:
                await self._plugins.notify(
                    "on_compact_end",
                    summary=summary,
                    messages_removed=boundary - 1,
                )

        except asyncio.CancelledError:
            logger.info("Compact cancelled", agent=self._agent_name)
        except Exception as e:
            logger.error("Compact failed", agent=self._agent_name, error=str(e))
        finally:
            self._compacting = False
            self._compact_task = None

    def _count_keep_messages(self, messages: list) -> int:
        """Count how many messages from the end to keep (live zone).

        Keeps at least keep_recent_turns worth of user/assistant pairs,
        plus any tool call/result messages between them.
        """
        turns = 0
        count = 0
        for msg in reversed(messages):
            count += 1
            if msg.role == "user":
                turns += 1
                if turns >= self.config.keep_recent_turns:
                    break
        return min(count, len(messages) - 1)  # Always keep system prompt

    def _format_messages_for_summary(self, messages: list) -> str:
        """Format messages into text for the summarization prompt."""
        parts = []
        for msg in messages:
            role = msg.role
            content = msg.content if isinstance(msg.content, str) else ""
            if not content and hasattr(msg, "content") and msg.content:
                # Handle multimodal: extract text parts
                if isinstance(msg.content, list):
                    content = " ".join(
                        p.text for p in msg.content if hasattr(p, "text")
                    )

            # Truncate very long tool results
            if role == "tool" and len(content) > 500:
                content = content[:500] + f"... ({len(content)} chars total)"

            if content:
                parts.append(f"[{role}]: {content}")

        return "\n\n".join(parts)

    async def _summarize(self, text: str) -> str:
        """Call LLM to produce a structured summary."""
        if not self._llm:
            return ""

        prompt_messages = [
            {"role": "system", "content": COMPACT_PROMPT},
            {"role": "user", "content": f"Summarize this conversation:\n\n{text}"},
        ]

        try:
            result = ""
            async for chunk in self._llm.chat(prompt_messages, stream=True):
                result += chunk
            return result.strip()
        except Exception as e:
            logger.error("Summarization LLM call failed", error=str(e))
            return ""

    def _emergency_truncate(self, messages: list) -> str:
        """Last resort: create a minimal summary from message roles/counts."""
        user_count = sum(1 for m in messages if m.role == "user")
        assistant_count = sum(1 for m in messages if m.role == "assistant")
        tool_count = sum(1 for m in messages if m.role == "tool")
        return (
            f"[Context auto-compact: {len(messages)} messages truncated "
            f"({user_count} user, {assistant_count} assistant, {tool_count} tool). "
            f"Use search_memory to retrieve specific details from session history.]"
        )

    def _splice_conversation(
        self, conversation: Any, boundary: int, summary: str
    ) -> None:
        """Atomic splice: replace compact zone with summary message."""
        messages = conversation.get_messages()

        # Build new message list:
        # [system_prompt] + [summary_as_assistant] + [live_zone]
        system_msg = messages[0]  # Always keep system prompt
        live_zone = messages[boundary:]  # Everything after boundary

        # Clear and rebuild
        conversation._messages.clear()
        conversation._messages.append(system_msg)

        # Add summary as an assistant message with a marker
        summary_msg = create_message(
            "assistant",
            f"[Previous context summary (compact round {self._compact_count + 1})]\n\n{summary}",
        )
        conversation._messages.append(summary_msg)

        # Restore live zone
        conversation._messages.extend(live_zone)

        # Update metadata
        conversation._metadata.message_count = len(conversation._messages)
        conversation._metadata.total_chars = conversation.get_context_length()
        conversation._metadata.updated_at = datetime.now()

    async def cancel(self) -> None:
        """Cancel any running compaction."""
        if self._compact_task and not self._compact_task.done():
            self._compact_task.cancel()
            try:
                await self._compact_task
            except (asyncio.CancelledError, Exception):
                pass
        self._compacting = False
        self._compact_task = None
