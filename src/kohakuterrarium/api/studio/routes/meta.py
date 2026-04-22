"""Meta routes — health + version for studio backend."""

from fastapi import APIRouter

router = APIRouter()


STUDIO_VERSION = "0.1.0"


@router.get("/health")
async def health() -> dict:
    """Liveness probe."""
    return {"ok": True}


@router.get("/version")
async def version() -> dict:
    """Return studio + core versions for the frontend's About panel."""
    try:
        from importlib.metadata import PackageNotFoundError, version as _pkg_version

        try:
            core_version = _pkg_version("kohakuterrarium")
        except PackageNotFoundError:
            core_version = "unknown"
    except Exception:
        core_version = "unknown"
    return {"studio": STUDIO_VERSION, "core": core_version}
