"""Retry, backoff, and dead-letter handling for the Drive dispatcher.

Split from :mod:`delivery` to respect the file-size cap. The concrete dispatcher
supplies the repository, config, clock, rng, and observer attributes.
"""

from datetime import datetime, timedelta

from kohakuterrarium.terrarium.drive.errors import DriveError
from kohakuterrarium.terrarium.drive.models import (
    SYSTEM_ACTOR,
    DriveDelivery,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.policy import (
    DeliveryFailureKind,
    RetryDisposition,
    classify_delivery_failure,
)
from kohakuterrarium.terrarium.drive.sink import emit_observation
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Dead-letter handling may block only non-terminal pursuit states.
BLOCKABLE_STATUSES = frozenset(
    {DriveStatus.ACTIVE, DriveStatus.WAITING, DriveStatus.PAUSED}
)


class DeliveryFailureMixin:
    """Classify a failed delivery attempt and apply its retry disposition."""

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
            case _:
                # Dead-letter and fail-closed dispositions both terminate delivery.
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
        """Block a non-terminal drive after its delivery dead-letters."""
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
        except DriveError as exc:
            # Dead-letter persistence remains authoritative if blocking races.
            logger.warning(
                "dead-letter block failed", drive_id=drive_id, error=str(exc)
            )

    def _backoff_seconds(self, attempt: int) -> float:
        retry = self._config.retry
        base = min(retry.initial_backoff_s * (2 ** (attempt - 1)), retry.max_backoff_s)
        # Symmetric jitter keeps the expected delay at the capped exponential base.
        delta = retry.jitter * (2 * self._rng.random() - 1)
        return max(0.0, base * (1 + delta))


__all__ = ["BLOCKABLE_STATUSES", "DeliveryFailureMixin"]
