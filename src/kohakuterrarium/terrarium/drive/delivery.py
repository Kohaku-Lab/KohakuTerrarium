"""Drive outbox dispatcher: claim, schedule, admit, settle, retry (design §5).

:class:`DriveDispatcher` is the mechanical delivery plane. It owns exactly one
background task per manager, wakes on manager mutations (outbox activity) plus a
bounded periodic scan for retry/lease timers, and never polls in a hot loop. For
each due delivery it re-verifies revision/epoch/assignment/readiness against the
claimed row (a stale row is superseded, never delivered), projects the §5.1
``TriggerEvent`` through the enabled registration, and hands it to a
:class:`DriveDeliverySink` — the Phase E seam over ``Creature.inject_event``.

Physical admission and turn settlement are separated (§5.2): the sink returns an
admission decision immediately and a settlement source resolved later. Settled
errors classify into retry-backoff (injected clock/rng) or, after
``max_attempts``, dead-letter plus a default Drive block (§5.6). Stopped
assignees are deferred with no failure count (§6.1). No LLM, no spec
interpretation, no ``datetime.now``/``random`` outside the injected clock/rng.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
from kohakuterrarium.terrarium.drive.errors import DriveError
from kohakuterrarium.terrarium.drive.models import (
    SYSTEM_ACTOR,
    DriveAssignment,
    DriveDelivery,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.policy import (
    DeliveryFailureKind,
    DriveScheduleItem,
    RetryDisposition,
    classify_delivery_failure,
    is_delivery_stale,
    schedule_sort_key,
)
from kohakuterrarium.terrarium.drive.registration_options import json_bytes
from kohakuterrarium.terrarium.drive.repository import Mutation
from kohakuterrarium.terrarium.drive.sink import (
    DriveDeliverySink,
    DriveObserver,
    Settlement,
    SettlementSource,
    SettlementStatus,
    emit_observation,
)
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot
from kohakuterrarium.terrarium.drive.store import DriveRepositoryClosedError
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# §5.1 event type is projected from the delivery reason; identity/revision/epoch
# stay framework-owned and cannot be patched by a registration projection.
DRIVE_READY_EVENT = "drive_ready"
DRIVE_RESUME_EVENT = "drive_resume"
DRIVE_RECOVERY_EVENT = "drive_recovery"
_EVENT_TYPE_BY_REASON = {"resume": DRIVE_RESUME_EVENT, "recovery": DRIVE_RECOVERY_EVENT}

# §6.1 uncertain-attempt warning that every recovery projection must carry.
RECOVERY_WARNING = (
    "A previous attempt may have executed side effects. Inspect current state "
    "and reconcile before repeating actions. Use the delivery ID as an "
    "idempotency key where supported."
)

# Non-terminal statuses a dead-letter default-block may move a Drive from (§5.6).
BLOCKABLE_STATUSES = frozenset(
    {DriveStatus.ACTIVE, DriveStatus.WAITING, DriveStatus.PAUSED}
)
# Delivery states that still hold a claim/lease this dispatcher must release.
_CLAIMED_STATE = "claimed"


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class DriveDispatcher:
    """One claim/dispatch loop over a repository outbox (design §5)."""

    def __init__(
        self,
        *,
        repository: Any,
        snapshot_provider: Callable[[], EnabledRegistrySnapshot | None],
        config: DriveRuntimeConfig,
        sink: DriveDeliverySink,
        clock: Callable[[], datetime],
        rng: Any,
        observer: DriveObserver | None = None,
        owner: str = "drive-dispatcher",
        readiness_generation: Callable[[str], int] | None = None,
        scan_callback: Callable[[], Awaitable[None]] | None = None,
        ack_callback: Callable[[str], Awaitable[None]] | None = None,
        lease_seconds: float = 30.0,
        scan_interval: float = 5.0,
    ) -> None:
        self._repo = repository
        self._snapshot_provider = snapshot_provider
        self._config = config
        self._sink = sink
        self._clock = clock
        self._rng = rng
        self._observer = observer
        self._owner = owner
        self._readiness_gen = readiness_generation or (lambda _drive_id: 0)
        self._scan_callback = scan_callback
        self._ack_callback = ack_callback
        self._lease_seconds = lease_seconds
        self._scan_interval = scan_interval
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopping = False
        # one admission per assignee at a time (§5.5) + fairness accounting.
        self._inflight: dict[str, str] = {}
        self._consecutive: dict[str, int] = {}
        self._settle_tasks: set[asyncio.Task] = set()

    # -- lifecycle -----------------------------------------------------------

    def notify(self) -> None:
        """Wake the claim loop after a manager mutation enqueued outbox work."""
        self._wake.set()

    async def start(self) -> None:
        """Idempotent: a second call while running is a no-op."""
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Idempotent: stop the loop, drain settlements, release every claim.

        Claim release opens a repository transaction, so it tolerates a repo
        whose companion closer already shut the executor (store close raced the
        stop): there is nothing to release against a closed repository."""
        task = self._task
        self._task = None
        if task is not None:
            self._stopping = True
            self._wake.set()
            try:
                await task
            except asyncio.CancelledError:  # pragma: no cover - defensive
                pass
        await self._drain_settlements()
        try:
            await self._release_claims()
        except DriveRepositoryClosedError:
            pass

    async def drain(self) -> None:
        """Await every in-flight settlement task (test + shutdown helper)."""
        await self._drain_settlements()

    async def _run(self) -> None:
        while not self._stopping:
            try:
                if self._scan_callback is not None:
                    await self._scan_callback()
                await self.dispatch_once()
            except DriveRepositoryClosedError:
                # The repository's executor was shut underneath the loop (a store
                # close / replacement raced ahead of stop()). A closed repo is a
                # quiesce signal, not a round failure: stop cleanly rather than
                # spinning and logging bogus round errors until stop() flags us.
                self._stopping = True
                break
            except Exception as exc:  # a bad round must not kill the loop
                logger.error("drive dispatch round failed", error=str(exc))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._scan_interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
        await self._drain_settlements()

    # -- one claim/dispatch round -------------------------------------------

    async def dispatch_once(self) -> None:
        """Claim due deliveries and dispatch them in deterministic order."""
        now = self._clock()
        limit = max(1, self._config.dispatcher_concurrency) * 4
        claimed = await self._repo.claim_deliveries(
            self._owner, limit, lease_seconds=self._lease_seconds, now=now
        )
        prepared: list[
            tuple[DriveDelivery, DriveRecord | None, DriveAssignment | None]
        ] = []
        for delivery in claimed:
            record = await self._repo.get(delivery.drive_id)
            assignment = await self._repo.get_assignment(delivery.drive_id)
            prepared.append((delivery, record, assignment))
        prepared.sort(key=lambda t: schedule_sort_key(_schedule_item(t[0], t[1])))
        dispatched_assignees: set[str] = set()
        for delivery, record, assignment in prepared:
            await self._consider(
                delivery, record, assignment, now, dispatched_assignees
            )

    async def _consider(
        self,
        delivery: DriveDelivery,
        record: DriveRecord | None,
        assignment: DriveAssignment | None,
        now: datetime,
        dispatched_assignees: set[str],
    ) -> None:
        gen = self._readiness_gen(delivery.drive_id)
        if is_delivery_stale(
            delivery, record, assignment, current_readiness_generation=gen
        ) or await self._already_admitted(delivery):
            # Stale row, or another delivery with the same §5.3 logical key already
            # reached the creature — one logical admission, never delivered twice.
            await self._repo.mark_delivery(delivery.delivery_id, "superseded", now=now)
            emit_observation(
                self._observer,
                "drive_delivery_superseded",
                delivery.drive_id,
                {"delivery_id": delivery.delivery_id},
            )
            return
        assignee = delivery.assignee_creature_id
        # One Drive admission per assignee per round and while one is in flight.
        if assignee in dispatched_assignees or assignee in self._inflight:
            return
        if len(self._inflight) >= self._config.dispatcher_concurrency:
            emit_observation(
                self._observer,
                "drive_backpressured",
                delivery.drive_id,
                {
                    "reason": "dispatcher_concurrency",
                    "delivery_id": delivery.delivery_id,
                },
            )
            return
        # Fairness: after N consecutive Drive turns, yield to queued foreign work.
        if (
            self._consecutive.get(assignee, 0)
            >= self._config.max_consecutive_drive_turns
        ):
            if self._sink.has_queued_foreign_work(assignee):
                self._consecutive[assignee] = 0
                emit_observation(
                    self._observer,
                    "drive_yielded",
                    delivery.drive_id,
                    {"assignee": assignee},
                )
                return
        try:
            event = self._build_event(delivery, record, assignment)
        except Exception as exc:
            await self._on_failure(
                delivery,
                DeliveryFailureKind.TRANSIENT,
                f"projection_failed: {exc}",
                now,
            )
            return
        dispatched_assignees.add(assignee)
        await self._dispatch(delivery, assignee, event, now)

    async def _already_admitted(self, delivery: DriveDelivery) -> bool:
        """Whether a *different* delivery with the same §5.3 logical key already
        reached the creature (admitted/acknowledged) — the repository-authoritative
        logical dedupe behind the at-least-once physical guarantee."""
        key = delivery.logical_key()
        for other in await self._repo.list_deliveries(delivery.drive_id):
            if other.delivery_id == delivery.delivery_id:
                continue
            if (
                other.state in ("admitted", "acknowledged")
                and other.logical_key() == key
            ):
                return True
        return False

    # -- physical dispatch + settlement -------------------------------------

    async def _dispatch(
        self, delivery: DriveDelivery, assignee: str, event: TriggerEvent, now: datetime
    ) -> None:
        self._inflight[assignee] = delivery.delivery_id
        try:
            outcome = await self._sink.deliver(
                assignee, event, delivery_id=delivery.delivery_id
            )
        except Exception as exc:
            self._inflight.pop(assignee, None)
            await self._on_failure(
                delivery, DeliveryFailureKind.TRANSIENT, f"sink_error: {exc}", now
            )
            return
        if not outcome.admitted:
            # Stopped assignee: defer, no failure count (§6.1). Back to pending so
            # a restart reconcile can redeliver; the lease stays this owner's.
            self._inflight.pop(assignee, None)
            self._consecutive[assignee] = 0
            await self._repo.mark_delivery(delivery.delivery_id, "pending", now=now)
            emit_observation(
                self._observer,
                "drive_delivery_deferred",
                delivery.drive_id,
                {"reason": "assignee_stopped", "delivery_id": delivery.delivery_id},
            )
            return
        await self._repo.mark_delivery(delivery.delivery_id, "admitted", now=now)
        self._consecutive[assignee] = self._consecutive.get(assignee, 0) + 1
        emit_observation(
            self._observer,
            "drive_delivery_admitted",
            delivery.drive_id,
            {"delivery_id": delivery.delivery_id},
        )
        if outcome.settlement is None:
            # Admitted with no settlement: leave admitted; reconcile classifies it
            # as an uncertain attempt if the lease is lost (§5.2, §6.1).
            self._inflight.pop(assignee, None)
            return
        task = asyncio.create_task(self._settle(delivery, assignee, outcome.settlement))
        self._settle_tasks.add(task)
        task.add_done_callback(self._settle_tasks.discard)

    async def _settle(
        self, delivery: DriveDelivery, assignee: str, source: SettlementSource
    ) -> None:
        try:
            settlement = await _resolve_settlement(source)
        except Exception as exc:  # a broken settlement source is a transient turn error
            settlement = Settlement(SettlementStatus.ERROR, {"error": str(exc)})
        now = self._clock()
        try:
            match settlement.status:
                case SettlementStatus.OK:
                    await self._repo.mark_delivery(
                        delivery.delivery_id, "acknowledged", now=now
                    )
                    emit_observation(
                        self._observer,
                        "drive_delivery_acknowledged",
                        delivery.drive_id,
                        {
                            "delivery_id": delivery.delivery_id,
                            "outcome": settlement.detail,
                        },
                    )
                    await self._notify_acknowledged(delivery.drive_id)
                case SettlementStatus.ERROR:
                    await self._on_failure(
                        delivery,
                        DeliveryFailureKind.TURN_ERROR,
                        settlement.detail.get("error", "turn_error"),
                        now,
                    )
                case SettlementStatus.INTERRUPTED:
                    if settlement.detail.get("interrupted_by_user"):
                        await self._repo.mark_delivery(
                            delivery.delivery_id,
                            "acknowledged",
                            reason="user_interrupted",
                            detail=settlement.detail,
                            now=now,
                        )
                        emit_observation(
                            self._observer,
                            "drive_delivery_acknowledged",
                            delivery.drive_id,
                            {
                                "delivery_id": delivery.delivery_id,
                                "outcome": settlement.detail,
                            },
                        )
                    else:
                        await self._on_failure(
                            delivery, DeliveryFailureKind.TRANSIENT, "interrupted", now
                        )
        finally:
            self._inflight.pop(assignee, None)
            self._wake.set()

    async def _notify_acknowledged(self, drive_id: str) -> None:
        """Post-ack continuation hook (design §4.4): let the manager re-evaluate
        the registration's readiness right after a turn settles. Fail-open — a
        re-arm failure must never break settlement bookkeeping."""
        if self._ack_callback is None:
            return
        try:
            await self._ack_callback(drive_id)
        except Exception as exc:
            logger.warning(
                "post-ack readiness re-evaluation failed",
                drive_id=drive_id,
                error=str(exc),
            )

    async def _on_failure(
        self,
        delivery: DriveDelivery,
        kind: DeliveryFailureKind,
        error: str,
        now: datetime,
    ) -> None:
        new_attempt = delivery.attempt + 1
        disposition = classify_delivery_failure(
            kind, attempt=new_attempt, max_attempts=self._config.retry.max_attempts
        )
        match disposition:
            case RetryDisposition.RETRY_BACKOFF:
                available_at = now + timedelta(
                    seconds=self._backoff_seconds(new_attempt)
                )
                await self._repo.mark_delivery(
                    delivery.delivery_id,
                    "retry_wait",
                    error=error,
                    available_at=available_at,
                    attempt=new_attempt,
                    now=now,
                )
                emit_observation(
                    self._observer,
                    "drive_delivery_retrying",
                    delivery.drive_id,
                    {
                        "delivery_id": delivery.delivery_id,
                        "attempt": new_attempt,
                        "available_at": available_at.isoformat(),
                    },
                )
            case RetryDisposition.DEFER:
                await self._repo.mark_delivery(delivery.delivery_id, "pending", now=now)
            case RetryDisposition.SUPERSEDE:
                await self._repo.mark_delivery(
                    delivery.delivery_id, "superseded", now=now
                )
            case _:  # DEAD_LETTER and FAIL_CLOSED both terminate the delivery
                await self._repo.mark_delivery(
                    delivery.delivery_id,
                    "dead_letter",
                    error=error,
                    reason="max_attempts_exhausted",
                    detail={"attempt": new_attempt},
                    now=now,
                )
                emit_observation(
                    self._observer,
                    "drive_delivery_dead_lettered",
                    delivery.drive_id,
                    {"delivery_id": delivery.delivery_id, "attempt": new_attempt},
                )
                await self._block_on_dead_letter(delivery.drive_id, now)

    async def _block_on_dead_letter(self, drive_id: str, now: datetime) -> None:
        """§5.6 default: a dead-lettered delivery blocks its Drive (system actor)."""
        record = await self._repo.get(drive_id)
        if record is None or record.status not in BLOCKABLE_STATUSES:
            return
        try:
            await self._repo.transition_drive(
                drive_id,
                DriveStatus.BLOCKED,
                expected_revision=record.revision,
                actor=SYSTEM_ACTOR,
                status_reason="delivery_dead_lettered",
                operation="dead_letter_block",
            )
            emit_observation(
                self._observer,
                "drive_status_changed",
                drive_id,
                {"status": "blocked", "reason": "dead_letter"},
            )
        except (
            DriveError
        ) as exc:  # blocking is best-effort; dead-letter already recorded
            logger.warning(
                "dead-letter block failed", drive_id=drive_id, error=str(exc)
            )

    def _backoff_seconds(self, attempt: int) -> float:
        retry = self._config.retry
        base = min(retry.initial_backoff_s * (2 ** (attempt - 1)), retry.max_backoff_s)
        # Symmetric jitter in [-jitter, +jitter] of base; rng.random() in [0, 1).
        delta = retry.jitter * (2 * self._rng.random() - 1)
        return max(0.0, base * (1 + delta))

    # -- event projection ----------------------------------------------------

    def _build_event(
        self,
        delivery: DriveDelivery,
        record: DriveRecord | None,
        assignment: DriveAssignment | None,
    ) -> TriggerEvent:
        reason = delivery.reason
        event_type = _EVENT_TYPE_BY_REASON.get(reason, DRIVE_READY_EVENT)
        projected: dict[str, Any] = {}
        prompt_override: str | None = None
        snapshot = self._snapshot_provider()
        entry = snapshot.for_kind(record.kind) if snapshot and record else None
        if entry is not None and entry.available and record is not None:
            project = getattr(entry.registration, "project_event", None)
            if callable(project):
                projection = project(record, assignment, reason)
                if projection is not None:
                    projected = dict(getattr(projection, "context", {}) or {})
                    prompt_override = getattr(projection, "prompt_override", None)
                    if json_bytes(projected) > self._config.presentation_max_bytes:
                        raise ValueError("projected view exceeds presentation budget")
        context = {
            "drive_id": record.drive_id if record else delivery.drive_id,
            "drive_kind": record.kind if record else None,
            "drive_revision": delivery.drive_revision,
            "lifecycle_epoch": delivery.lifecycle_epoch,
            "delivery_id": delivery.delivery_id,
            "assignment_id": delivery.assignment_id,
            "delivery_reason": reason,
            # Bounded projected view is namespaced so it can never overwrite the
            # framework identity fields above (design §5.1).
            "drive": projected,
        }
        if reason == "recovery":
            prompt_override = (
                RECOVERY_WARNING
                if not prompt_override
                else f"{RECOVERY_WARNING}\n\n{prompt_override}"
            )
        return TriggerEvent(
            type=event_type,
            content="",
            context=context,
            prompt_override=prompt_override,
            stackable=False,
        )

    # -- shutdown helpers ----------------------------------------------------

    async def _drain_settlements(self) -> None:
        tasks = list(self._settle_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _release_claims(self) -> None:
        """Return this owner's claimed-but-unadmitted deliveries to pending and
        clear its leases so a stop never strands a claim (design §6.4)."""
        async with self._repo.transaction() as txn:
            now = self._clock()
            mutation = Mutation()
            leased: set[str] = set()
            for record in await txn.all_drives():
                assignment = await txn.get_assignment(record.drive_id)
                if assignment is not None and assignment.lease_owner == self._owner:
                    leased.add(record.drive_id)
                    mutation.assignments.append(
                        replace(
                            assignment,
                            lease_owner=None,
                            lease_expires_at=None,
                            updated_at=now,
                        )
                    )
            for delivery in await txn.all_deliveries():
                if delivery.state == _CLAIMED_STATE and delivery.drive_id in leased:
                    mutation.deliveries.append(replace(delivery, state="pending"))
            if mutation.assignments or mutation.deliveries:
                await txn.apply(mutation)


def _schedule_item(
    delivery: DriveDelivery, record: DriveRecord | None
) -> DriveScheduleItem:
    """Build the §5.5 scheduler key input; a missing record sorts by the
    delivery's own timing at priority zero (it will be superseded anyway)."""
    priority = record.priority if record is not None else 0
    created_at = record.created_at if record is not None else delivery.created_at
    return DriveScheduleItem(
        drive_id=delivery.drive_id,
        available_at=delivery.available_at,
        priority=priority,
        created_at=created_at,
        last_delivered_at=delivery.admitted_at,
    )


async def _resolve_settlement(source: SettlementSource) -> Settlement:
    if source is None:  # caller guards this; kept total for safety
        return Settlement(SettlementStatus.OK)
    awaitable = source() if callable(source) else source
    return await awaitable
