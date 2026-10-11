"""Whole-request byte budget: a configured target plus a ceiling learned from refusals."""

from dataclasses import dataclass
from typing import Any

from kohakuterrarium.llm.image_preparation import IMAGE_PREPARER, body_bytes
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# A refused request lowers the ceiling to this fraction of its measured size.
SHRINK_NUMERATOR, SHRINK_DENOMINATOR = 3, 4
MIN_REQUEST_BYTES = 64 * 1024
MAX_REQUEST_SHRINKS = 3


@dataclass(frozen=True)
class RequestMeasure:
    """Measured size of one outbound request; ``image_count`` is ``None`` when unscanned."""

    bytes: int
    image_count: int | None


class RequestCeiling:
    """Byte ceiling learned from size refusals, shared by a provider, its forks and siblings.

    Lowered only from recovery on the event loop; request fitting threads only read it.
    """

    def __init__(self) -> None:
        self.max_bytes: int | None = None

    def lower(self, refused_bytes: int) -> int:
        """Lower the ceiling below ``refused_bytes``; never raises it. Returns the ceiling."""
        candidate = max(
            MIN_REQUEST_BYTES, refused_bytes * SHRINK_NUMERATOR // SHRINK_DENOMINATOR
        )
        if self.max_bytes is None or candidate < self.max_bytes:
            self.max_bytes = candidate
        return self.max_bytes


def effective_target(configured: int | None, learned: int | None) -> int | None:
    """The tighter of the configured target and the learned ceiling; ``None`` if neither."""
    values = [value for value in (configured, learned) if value]
    return min(values) if values else None


def fit_request(
    body: dict[str, Any], max_bytes: int | None, *, provider: str = ""
) -> tuple[dict[str, Any], RequestMeasure]:
    """Compress inline images of ``body`` toward ``max_bytes`` and measure the result.

    ``max_bytes`` of ``None``/``0`` leaves the body untouched and only measures
    it. Pure CPU work; callers run it off the event loop.
    """
    if not max_bytes:
        return body, RequestMeasure(body_bytes(body), None)
    prepared, stats, size, target = IMAGE_PREPARER.fit(body, max_bytes=max_bytes)
    if stats.changed_count:
        logger.info(
            "Request images compressed",
            provider=provider,
            image_count=stats.image_count,
            compressed=stats.changed_count,
            original_bytes=stats.original_bytes,
            prepared_bytes=stats.prepared_bytes,
            request_bytes=size,
            image_byte_target=target,
        )
    if size > max_bytes:
        logger.warning(
            "Request remains above its byte target after image compression",
            provider=provider,
            request_bytes=size,
            target_bytes=max_bytes,
        )
    return prepared, RequestMeasure(size, stats.image_count)
