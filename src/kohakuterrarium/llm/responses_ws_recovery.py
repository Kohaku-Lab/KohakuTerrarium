"""Bounded recovery and attempt lifecycle for one Responses request."""

import asyncio
import time
from contextlib import aclosing
from copy import deepcopy
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import uuid4

from websockets.exceptions import ConnectionClosed

from kohakuterrarium.llm.model_recovery_status import notify_model_recovery
from kohakuterrarium.llm.recovery import RetryPolicy, backoff_delay
from kohakuterrarium.llm.responses_tools import prepare_request_tools
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Seconds a cancelled request waits for its close handshake before propagating.
CANCEL_CLOSE_GRACE = 1.0


def event_field(value: Any, name: str) -> Any:
    """Read a wire field from SDK objects or JSON dictionaries."""
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def retryable_close(error: BaseException) -> bool:
    """Check both close frames through SDK exception wrappers."""
    seen: set[int] = set()
    while id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, ConnectionClosed):
            return all(
                frame is None or frame.code in {1000, 1001, 1011, 1012, 1013, 1014}
                for frame in (error.rcvd, error.sent)
            )
        cause = error.__cause__ or error.__context__
        if cause is None:
            break
        error = cause
    return True


class ResponsesWSError(Exception):
    """A failed request, including submission uncertainty across attempts."""

    def __init__(
        self,
        message: str,
        *,
        mid_stream: bool,
        transport: bool = False,
        submitted: bool = True,
        code: str = "",
        status_code: int | None = None,
        raw_event: Any = None,
        last_event_type: str = "",
        retire_connection: bool = False,
        cache_miss: bool = False,
        capability_mismatch: bool = False,
    ) -> None:
        super().__init__(message)
        self.mid_stream = mid_stream
        self.transport = transport
        self.submitted = submitted
        self.code = code
        self.status_code = status_code
        self.raw_event = raw_event
        self.last_event_type = last_event_type
        self.body = {"code": code, "message": message}
        self.retire_connection = retire_connection or transport
        self.cache_miss = cache_miss
        # The connection cannot serve the hosted tools this request declares.
        self.capability_mismatch = capability_mismatch


class WSRecovery:
    """Own submission budget, delivery state, and disposable attempts."""

    def __init__(
        self,
        policy: RetryPolicy,
        notify: Callable[[str | None], Awaitable[None]] | None = None,
        *,
        raw_delivery: bool = False,
    ) -> None:
        self.policy = policy
        self.notify = notify
        self.raw_delivery = raw_delivery
        self.delivered = False
        self.submitted = False
        self.submissions = 0
        self.uncertain_replays = 0
        self.http = False
        self._phase: str | None = None
        self._request_id = uuid4().hex
        self._started_at = time.time()
        self._sequence = 0

    def record_submission(self) -> None:
        """Charge an attempted send, including an uncertain failed send."""
        if not self.has_budget:
            raise ResponsesWSError(
                "Responses recovery budget exhausted",
                mid_stream=self.delivered,
                submitted=self.submitted,
            )
        self.submissions += 1
        self.submitted = True

    @property
    def has_budget(self) -> bool:
        """Whether another wire submission fits the logical request budget."""
        return self.submissions < 1 + max(0, self.policy.max_retries)

    async def status(self, phase: str | None) -> None:
        """Notify the current request's observer when recovery phase changes."""
        if phase == self._phase:
            return
        self._phase = phase
        self._sequence += 1
        await notify_model_recovery(
            {
                "request_id": self._request_id,
                "request_started_at": self._started_at,
                "sequence": self._sequence,
                "phase": phase,
            }
        )
        if self.notify is not None:
            await self.notify(phase)

    async def run(
        self,
        session: Any,
        base_event: dict,
        items: list[dict],
        pairing_fix: Callable,
        reset_attempt: Callable[[], None] | None,
    ) -> AsyncIterator[Any]:
        """Drive isolated attempts while retaining the session's exclusive lock."""
        base_event, items = deepcopy(base_event), deepcopy(items)
        base_event, blocked_tools = prepare_request_tools(base_event)
        replayable = not base_event.get("background") and not blocked_tools
        delta = session._compute_delta(items)
        connect_failures = 0
        try:
            while True:
                if reset_attempt is not None:
                    reset_attempt()
                try:
                    async with aclosing(
                        session._run_turn(base_event, items, pairing_fix, delta, self)
                    ) as stream:
                        async for event in stream:
                            await self.status(None)
                            if self.raw_delivery:
                                self.delivered = True
                            yield event
                    return
                except ResponsesWSError as exc:
                    self.submitted |= exc.submitted
                    exc.submitted = self.submitted
                    if exc.retire_connection:
                        await session.close()
                    budget = self.submissions < 1 + max(0, self.policy.max_retries)
                    rejected = exc.cache_miss and not exc.mid_stream
                    transient = (
                        exc.retire_connection
                        and not exc.capability_mismatch
                        and retryable_close(exc)
                    )
                    # Capability rejections replay only before any server event.
                    capability_retry = (
                        exc.capability_mismatch and not exc.mid_stream and replayable
                    )
                    if not exc.submitted:
                        connect_failures += 1
                    permitted = (
                        self.policy.max_retries > 0
                        and budget
                        and not self.delivered
                        and (
                            rejected
                            or capability_retry
                            or (
                                transient
                                and connect_failures < 2
                                and (not exc.submitted or replayable)
                                and self.uncertain_replays < 1
                            )
                        )
                    )
                    if not permitted:
                        if self.policy.max_retries <= 0:
                            reason = "retries_disabled"
                        elif not budget:
                            reason = "submission_budget_exhausted"
                        elif self.delivered:
                            reason = "content_delivered"
                        elif not transient and not rejected and not capability_retry:
                            reason = "non_retryable_error"
                        elif connect_failures >= 2:
                            reason = "connection_attempts_exhausted"
                        elif exc.submitted and base_event.get("background"):
                            reason = "background_request"
                        elif exc.submitted and blocked_tools:
                            reason = "tool_replay_forbidden"
                        else:
                            reason = "uncertain_replay_exhausted"
                        logger.warning(
                            "Responses WS recovery stopped",
                            request_id=self._request_id,
                            code=exc.code,
                            last_event_type=exc.last_event_type,
                            submitted=self.submitted,
                            delivered=self.delivered,
                            submissions=self.submissions,
                            stop_reason=reason,
                            blocked_tools=blocked_tools,
                            max_retries=self.policy.max_retries,
                            uncertain_replays=self.uncertain_replays,
                            received_events=exc.mid_stream,
                        )
                        raise
                    if not (rejected or capability_retry) and exc.submitted:
                        self.uncertain_replays += 1
                    logger.warning(
                        "Responses WS recovering",
                        request_id=self._request_id,
                        code=exc.code,
                        last_event_type=exc.last_event_type,
                        submitted=self.submitted,
                        submissions=self.submissions,
                        uncertain_replays=self.uncertain_replays,
                    )
                    delta = None
                    delay = (
                        backoff_delay(max(1, self.submissions), self.policy)
                        if exc.transport
                        else 0
                    )
                    await self.status("waiting" if delay else "reconnecting")
                    if delay:
                        await asyncio.sleep(delay)
                        await self.status("reconnecting")
        except (asyncio.CancelledError, GeneratorExit):
            await session.close(grace=CANCEL_CLOSE_GRACE)
            raise
        finally:
            await self.status(None)
