"""Request-level helpers shared by persisted-session resume routes."""

from typing import Any

from fastapi import HTTPException, Request


def resume_intent(body: Any) -> str:
    """Return a stable semantic identity for coordinator singleflight."""

    def flat(values: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted((str(key), str(value)) for key, value in (values or {}).items())
        )

    nested = tuple(
        sorted(
            (str(member_id), flat(replacements))
            for member_id, replacements in (
                body.member_workspace_overrides or {}
            ).items()
        )
    )
    members = tuple(
        sorted((member.sid, member.on_node) for member in (body.members or []))
    )
    return repr(
        (
            body.on_node or "_host",
            body.pwd,
            members,
            flat(body.workspace_overrides),
            nested,
            flat(body.member_pwd_overrides),
        )
    )


def partial_dirty(exc: BaseException, failures: list[str]) -> HTTPException:
    """Build the fail-closed response used when compensation is incomplete."""
    return HTTPException(
        status_code=502,
        detail={
            "code": "partial_dirty",
            "message": str(exc),
            "rollback_failures": failures,
            "cleanup_failures": failures,
        },
    )


def reject_lab_host_target(request: Request, on_node: str) -> None:
    """Reject host-local adoption before resolving any saved-session path."""
    if (
        on_node != "_host"
        or getattr(request.app.state, "lab_mode", "standalone") != "lab-host"
    ):
        return
    raise HTTPException(
        status_code=400,
        detail=(
            "lab-host mode runs no agents on the host — resume on a "
            "worker node (pass on_node=<worker name>)"
        ),
    )


__all__ = ["partial_dirty", "reject_lab_host_target", "resume_intent"]
