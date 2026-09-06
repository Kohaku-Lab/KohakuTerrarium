"""Process-wide file-descriptor limit helpers.

``resource`` exists only on POSIX; every helper degrades to a no-op elsewhere.
"""

try:
    import resource
except ImportError:
    resource = None

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_TARGET = 65536
# Ceilings tried in turn when the kernel rejects the requested value.
_FALLBACK_CEILINGS = (10240, 4096, 1024)


def soft_fd_limit() -> int | None:
    """Return the soft descriptor limit, or ``None`` when unlimited or unsupported."""
    if resource is None:
        return None
    soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft == resource.RLIM_INFINITY:
        return None
    return soft


def raise_fd_limit(target: int = DEFAULT_TARGET, *, log: bool = False) -> int | None:
    """Lift the soft descriptor limit toward ``target``.

    Returns the resulting soft limit, or ``None`` when unlimited or
    unsupported. Values the kernel rejects fall through to smaller ceilings.
    """
    if resource is None:
        return None
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft == resource.RLIM_INFINITY:
        return None
    if soft >= target:
        return soft
    ceiling = target if hard == resource.RLIM_INFINITY else min(hard, target)
    for candidate in (ceiling, *_FALLBACK_CEILINGS):
        if candidate <= soft:
            break
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (candidate, hard))
        except (ValueError, OSError):
            continue
        soft = candidate
        break
    if log:
        logger.info(
            "fd limit configured",
            soft=soft,
            hard="unlimited" if hard == resource.RLIM_INFINITY else hard,
        )
    return soft
