"""
Non-blocking auto-compact system.

Automatically summarizes old conversation context in the background
when approaching token limits. The agent keeps working during compaction.

Design:
  - Two-zone model: compact zone (old, will be summarized) + live zone (recent, untouched)
  - Background task: LLM summarizes the compact zone asynchronously
  - Atomic splice: when summary is ready, replaces compact zone
  - Incremental: each round's summary includes the previous summary
  - Failure preserves context unchanged — no emergency truncation, no
    silent data loss; the agent keeps the full context if the LLM call
    fails so the user can retry / switch model / etc.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.core.compact_splice import (
    count_keep_messages,
    prefix_fingerprint,
    splice_conversation,
)
from kohakuterrarium.core.compact_text import (
    COMPACT_PROMPT,
    extract_message_text,
    format_messages_for_summary,
)
from kohakuterrarium.core.conversation_elide import elide_after_compact
from kohakuterrarium.core.single_flight import SingleFlightDispatch, SingleFlightLease
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Defaults for auto-compaction.
# max_tokens is the model's context window size. Set this to match
# the model you are using (e.g. 272000 for gpt-5.4, 1000000 for Gemini 3).
# threshold triggers compaction when prompt_tokens reaches this fraction
# of max_tokens. If context somehow exceeds max_tokens, emergency truncation.
DEFAULT_MAX_TOKENS = 256_000
DEFAULT_THRESHOLD = 0.80  # compact when prompt_tokens >= 80% of max_tokens
DEFAULT_KEEP_RECENT = 8  # keep last 8 turns raw (not summarized)

# Usage from an LLM round that STARTED before a splice can land long
# after it (long rounds outlive the cooldown). Within this window after
# a splice, a trigger must be corroborated by the live conversation.
STALE_USAGE_WINDOW_SECONDS = 900.0


@dataclass
class CompactConfig:
    """Configuration for auto-compaction."""

    max_tokens: int = DEFAULT_MAX_TOKENS
    threshold: float = DEFAULT_THRESHOLD
    keep_recent_turns: int = DEFAULT_KEEP_RECENT
    enabled: bool = True
    cooldown_seconds: float = 30.0
    # If set, use a different model for summarization (cheaper/faster)
    compact_model: str | None = None


class CompactManager:
    """Manage non-blocking context compaction after LLM calls."""

    def __init__(self, config: CompactConfig | None = None):
        self.config = config or CompactConfig()
        self._dispatch = SingleFlightDispatch()
        self._compact_task: asyncio.Task | None = None
        self._active_lease: SingleFlightLease | None = None
        self._last_compact_time: float = 0
        self._compact_count: int = 0
        self._last_summary_error: str = ""
        # Why ``trigger_compact`` last returned ``False``. Read by the
        # ``/compact`` slash command (``builtins/user_commands/compact.py``)
        # to surface a precise reason instead of the generic "busy"
        # message. One of: ``""`` (success or never called),
        # ``"no_controller"``, ``"too_short"``, ``"busy"``.
        self._last_skip_reason: str = ""
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
        return self._dispatch.is_running

    def should_compact(self, prompt_tokens: int = 0) -> bool:
        """Check if compaction should be triggered.

        Uses prompt_tokens from the last LLM call. Token-based only,
        no character estimation fallback.
        """
        if not self.config.enabled or self.is_compacting:
            return False
        if self._last_compact_time:
            elapsed = time.time() - self._last_compact_time
            if elapsed < self.config.cooldown_seconds:
                return False

        if prompt_tokens <= 0:
            return False
        if prompt_tokens < self.config.max_tokens * self.config.threshold:
            return False
        return not self._usage_is_stale_after_splice()

    def _usage_is_stale_after_splice(self) -> bool:
        """True when a claimed over-threshold usage can't be justified
        by the live conversation shortly after a splice (the usage came
        from a round that started pre-splice)."""
        if not self._last_compact_time or not self._controller:
            return False
        if time.time() - self._last_compact_time > STALE_USAGE_WINDOW_SECONDS:
            return False
        try:
            messages = self._controller.conversation.get_messages()
        except Exception:
            return False
        chars = sum(len(extract_message_text(m) or "") for m in messages)
        # ``chars // 2`` deliberately over-estimates tokens so only a
        # conversation FAR below threshold vetoes the trigger.
        return chars // 2 < self.config.max_tokens * self.config.threshold

    def trigger_compact(self) -> bool:
        """Start compaction as a background task.

        Returns ``True`` only when this call actually dispatches the
        single allowed compact job. If another compact is already
        running, the attempt is ignored immediately and ``False`` is
        returned.

        Emits ``compact_start`` up-front, then runs background
        summarization, then ``compact_complete`` (or ``compact_skipped``
        if the conversation is too short to benefit).

        Pre-check short-conversation case BEFORE acquiring the
        single-flight lease or emitting ``compact_start``. Otherwise the
        UI gets stuck on a "compacting..." banner because the background
        task returns via the early-out in ``_run_compact`` without
        emitting a completion event. Terrariums hit this path often —
        the root agent's conversation is typically just orchestration so
        ``/compact`` on root has "nothing to compact".
        """
        if not self._controller:
            self._last_skip_reason = "no_controller"
            return False

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
            self._last_skip_reason = "too_short"
            return False

        lease = self._dispatch.try_acquire()
        if lease is None:
            logger.debug(
                "Compact trigger ignored — already running",
                agent=self._agent_name,
            )
            self._last_skip_reason = "busy"
            if self._output_router:
                self._output_router.notify_activity(
                    "compact_decision",
                    f"[{self._agent_name}] ignored (busy)",
                    metadata={
                        "reason": "busy",
                        "tokens_before": 0,
                        "tokens_after": 0,
                        "skipped": True,
                    },
                )
            return False

        self._active_lease = lease

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

        self._last_skip_reason = ""
        self._compact_task = asyncio.create_task(self._run_compact(lease))
        logger.info(
            "Auto-compact triggered",
            agent=self._agent_name,
            compact_count=self._compact_count + 1,
        )
        return True

    def _emit_compact_skipped(self, reason: str, message: str) -> None:
        """Terminal for a started round that did not complete. Every
        ``compact_start`` must be closed by exactly one terminal
        (``compact_complete`` or ``compact_skipped``) — FE / TUI / rich
        CLI all keep a "compacting…" indicator open until one lands."""
        if self._output_router:
            self._output_router.notify_activity(
                "compact_skipped",
                message,
                metadata={
                    "reason": reason,
                    "round": self._compact_count + 1,
                },
            )

    async def _run_compact(self, lease: SingleFlightLease | None = None) -> None:
        """Background compaction task."""
        if lease is None:
            lease = self._dispatch.try_acquire()
            if lease is None:
                logger.debug(
                    "Compact run ignored — already running",
                    agent=self._agent_name,
                )
                return
        self._active_lease = lease
        terminal_sent = False
        abort_reason = "aborted"
        try:
            conversation = self._controller.conversation
            messages = conversation.get_messages()

            # Find boundary: keep system prompt + last N turns
            # A "turn" is roughly: user message + assistant response + tool calls
            keep_count = self._count_keep_messages(messages)
            boundary = len(messages) - keep_count
            # Tool-safe boundary BEFORE summarization — adjusting at
            # splice time would keep already-summarized content raw
            # (duplicated in summary AND live zone).
            while boundary > 1 and getattr(messages[boundary], "role", None) == "tool":
                boundary -= 1

            if boundary <= 1:
                logger.debug("Not enough messages to compact")
                self._emit_compact_skipped("nothing_to_compact", "Nothing to compact")
                terminal_sent = True
                return

            # Compact zone: messages[1:boundary] (skip system at index 0)
            # Live zone: messages[boundary:]
            compact_messages = messages[1:boundary]

            if not compact_messages:
                self._emit_compact_skipped("nothing_to_compact", "Nothing to compact")
                terminal_sent = True
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
                    self._emit_compact_skipped(
                        "plugin_veto", "Compaction vetoed by plugin"
                    )
                    terminal_sent = True
                    logger.info(
                        "Compact vetoed by plugin(s)",
                        agent=self._agent_name,
                    )
                    # Cooldown applies to vetos too. Without this, the
                    # next ``should_compact`` call would re-trigger
                    # immediately on every turn while the conversation
                    # stays above threshold — wasting LLM/plugin calls
                    # and emitting a flood of ``compact_skipped`` events.
                    self._last_compact_time = time.time()
                    return

            # Build the text to summarize
            summary_input = self._format_messages_for_summary(compact_messages)
            expected_fingerprint = prefix_fingerprint(compact_messages)

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
                            "error": self._last_summary_error
                            or "Summarization LLM call failed. Context was NOT modified.",
                        },
                    )
                self._emit_compact_skipped(
                    "summary_failed",
                    "Summarization failed — context preserved unchanged",
                )
                terminal_sent = True
                return

            # The summarizer ran for a while — /clear, attach, or an
            # edit flow may have REPLACED the controller's conversation
            # object. Splicing (and persisting) the captured detached
            # object would report success without compacting anything.
            if (
                self._controller is not None
                and self._controller.conversation is not conversation
            ):
                logger.warning(
                    "Compact aborted — conversation object replaced "
                    "while summarizing",
                    agent=self._agent_name,
                )
                self._emit_compact_skipped(
                    "conversation_replaced",
                    "Conversation was replaced while summarizing",
                )
                terminal_sent = True
                self._last_compact_time = time.time()
                return

            # Atomic splice: replace compact zone with summary
            applied = self._splice_conversation(
                conversation,
                boundary,
                summary,
                expected_last=compact_messages[-1],
                expected_fingerprint=expected_fingerprint,
            )
            if not applied:
                logger.warning(
                    "Compact splice aborted — conversation changed shape "
                    "while summarizing",
                    agent=self._agent_name,
                )
                self._emit_compact_skipped(
                    "conversation_changed",
                    "Conversation changed while summarizing — will retry later",
                )
                terminal_sent = True
                self._last_compact_time = time.time()
                return

            self._compact_count += 1
            self._last_compact_time = time.time()
            # The next round's usage still measures the pre-splice
            # prompt — drop it so it can't re-trigger a compact.
            if self._controller is not None:
                self._controller._last_usage = {}

            # Compact summarizes the compact zone but leaves the live zone
            # (recent turns) untouched; elide stale tool results there so
            # the post-compact prompt actually drops below the threshold.
            elide_after_compact(self._controller)

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
            terminal_sent = True

            # Save conversation snapshot with post-compact version
            # (The compact_complete event is already recorded by SessionOutput
            # via notify_activity above — no need to append_event separately.)
            if self._session_store:
                # Overwrite conversation snapshot with post-compact version
                # so resume gets the compacted conversation, not the full one
                # The splice runs mid-turn: keep the in-flight tail
                # announcement so resume can reconstruct the active round.
                try:
                    self._session_store.save_conversation(
                        self._agent_name,
                        conversation.to_messages(
                            preserve_pending_tail=True, include_metadata=True
                        ),
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to save compacted conversation",
                        error=str(e),
                        exc_info=True,
                    )

                # Persist compact_count for resume continuity and
                # stamp the snapshot watermark to the latest event so
                # resume can trust the compacted snapshot. Persisting
                # ``last_compact_time`` lets resume restore the cooldown
                # window — without it, every resume would permit an
                # immediate re-compact regardless of how recent the last
                # one was (audit finding 3j).
                try:
                    last_event_id = 0
                    for evt in self._session_store.get_events(self._agent_name):
                        eid = evt.get("event_id")
                        if isinstance(eid, int) and eid > last_event_id:
                            last_event_id = eid
                    self._session_store.save_state(
                        self._agent_name,
                        compact_count=self._compact_count,
                    )
                    self._session_store.state[
                        f"{self._agent_name}:snapshot_event_id"
                    ] = last_event_id
                    self._session_store.state[
                        f"{self._agent_name}:last_compact_time"
                    ] = self._last_compact_time
                except Exception as e:
                    logger.warning(
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
            abort_reason = "cancelled"
            logger.info("Compact cancelled", agent=self._agent_name)
        except Exception as e:
            abort_reason = "error"
            logger.error("Compact failed", agent=self._agent_name, error=str(e))
        finally:
            if not terminal_sent:
                try:
                    self._emit_compact_skipped(
                        abort_reason, f"Compaction {abort_reason}"
                    )
                except Exception:  # pragma: no cover - defensive
                    pass
            self._dispatch.release(lease)
            if self._active_lease is lease:
                self._active_lease = None
            self._compact_task = None

    def _count_keep_messages(self, messages: list) -> int:
        return count_keep_messages(messages, self.config.keep_recent_turns)

    def _format_messages_for_summary(self, messages: list) -> str:
        return format_messages_for_summary(messages)

    def _summary_max_tokens(self) -> int:
        """Return a conservative output cap for the summarization request.

        Compact runs near the model's context ceiling. Reusing the
        profile's full normal ``max_output`` budget can make the summary
        request exceed the context window even when the regular turn just
        fit. Keep the summary budget intentionally small.
        """
        max_context = max(int(self.config.max_tokens or DEFAULT_MAX_TOKENS), 1024)
        return max(512, min(4096, max_context // 64))

    async def _summarize(self, text: str) -> str:
        """Call LLM to produce a structured summary."""
        if not self._llm:
            self._last_summary_error = (
                "No LLM available for compaction. Context was NOT modified."
            )
            return ""

        prompt_messages = [
            {"role": "system", "content": COMPACT_PROMPT},
            {"role": "user", "content": f"Summarize this conversation:\n\n{text}"},
        ]
        summary_max_tokens = self._summary_max_tokens()

        try:
            self._last_summary_error = ""
            result = ""
            async for chunk in self._llm.chat(
                prompt_messages,
                stream=True,
                max_tokens=summary_max_tokens,
            ):
                result += chunk
            return result.strip()
        except Exception as e:
            self._last_summary_error = (
                f"Summarization LLM call failed: {e}. Context was NOT modified."
            )
            logger.error(
                "Summarization LLM call failed",
                error=str(e),
                summary_max_tokens=summary_max_tokens,
            )
            return ""

    def _splice_conversation(
        self,
        conversation: Any,
        boundary: int,
        summary: str,
        expected_last: Any = None,
        expected_fingerprint: tuple | None = None,
    ) -> bool:
        """Atomic splice — see :func:`compact_splice.splice_conversation`."""
        return splice_conversation(
            conversation,
            boundary,
            summary,
            self._compact_count + 1,
            expected_last=expected_last,
            expected_fingerprint=expected_fingerprint,
        )

    async def wait_for_current(self) -> None:
        """Await the in-flight compact round, if any."""
        task = self._compact_task
        if task is not None and not task.done():
            try:
                await asyncio.shield(task)
            except Exception:
                pass

    async def cancel(self) -> None:
        """Cancel any running compaction."""
        if self._compact_task and not self._compact_task.done():
            self._compact_task.cancel()
            try:
                await self._compact_task
            except (asyncio.CancelledError, Exception):
                pass
        self._dispatch.force_release()
        self._active_lease = None
        self._compact_task = None
