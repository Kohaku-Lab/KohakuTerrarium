"""Auth routes — mounted under ``/api/auth/*``.

Surface:

| Method   | Path                          | Auth                                                    |
|----------|-------------------------------|---------------------------------------------------------|
| GET      | ``/capabilities``             | none — probe                                            |
| POST     | ``/register``                 | varies by ``registration`` mode                         |
| POST     | ``/login``                    | password                                                |
| POST     | ``/logout``                   | session cookie                                          |
| GET      | ``/me``                       | current user                                            |
| POST     | ``/me/password``              | current user (password change)                          |
| GET      | ``/tokens``                   | current user                                            |
| POST     | ``/tokens``                   | current user (creates new API token; plaintext once)    |
| DELETE   | ``/tokens/{id}``              | current user (own token only) OR admin                  |
| GET      | ``/users``                    | admin                                                   |
| POST     | ``/users``                    | admin (CLI-equivalent registration)                     |
| PATCH    | ``/users/{id}``               | admin (role / is_active)                                |
| DELETE   | ``/users/{id}``               | admin                                                   |
| POST     | ``/invitations``              | admin                                                   |
| GET      | ``/invitations``              | admin                                                   |
| DELETE   | ``/invitations/{id}``         | admin                                                   |
"""

from typing import Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

import dataclasses
import secrets

import kohakuterrarium.api.auth.invitations as invitations_db
import kohakuterrarium.api.auth.sessions as sessions_db
import kohakuterrarium.api.auth.tokens as tokens_db
import kohakuterrarium.api.auth.users as users_db
from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.auth.config_write import write_auth_section
from kohakuterrarium.api.auth.db import connection
from kohakuterrarium.api.auth.dependencies import (
    SESSION_COOKIE_NAME,
    get_auth_config,
    get_current_user,
)
from kohakuterrarium.api.auth.models import User
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


_CAPABILITIES_SCHEMA = 1


# ---------------------------------------------------------------------------
# Capabilities — unauthenticated
# ---------------------------------------------------------------------------


@router.get("/capabilities")
def capabilities(
    auth_config: AuthConfig = Depends(get_auth_config),
) -> dict[str, object]:
    """Advertise which auth layers the host has enabled.

    Unauthenticated by design — the response carries no secrets, only
    the enabled flags + mode metadata.
    """
    return {
        "schema": _CAPABILITIES_SCHEMA,
        "auth": auth_config.as_capabilities_dict(),
    }


# ---------------------------------------------------------------------------
# Pydantic request / response shapes
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=1)
    invitation_token: str = Field(
        default="", description="Required in invite_only mode"
    )


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class AdminUserCreateRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=1)
    role: Literal["user", "admin"] = "user"


class AdminUserPatchRequest(BaseModel):
    role: Literal["user", "admin"] | None = None
    is_active: bool | None = None


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=1)


class InvitationCreateRequest(BaseModel):
    role: Literal["user", "admin"] = "user"
    expires_in_hours: int | None = Field(default=None, ge=1, le=24 * 365)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_admin(user: User) -> None:
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"error": "admin_only", "message": "admin role required"},
        )


def _user_public(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }


def _set_session_cookie(
    response: Response,
    session_id: str,
    expires_at: str,
) -> None:
    """Set the session cookie with the documented attributes.

    - ``HttpOnly`` — no JS access (XSS hardening).
    - ``SameSite=Lax`` — sent on same-site navigations, blocked on
      most cross-site (CSRF mitigation; the user explicitly clicks a
      link to the host).
    - ``Secure`` is NOT forced here because operators behind a reverse
      proxy may terminate TLS upstream; the proxy adds the flag when
      the request was originally HTTPS.  For local desktop on
      loopback, the cookie ships without Secure and that's fine.
    """
    # Path defaults to "/" so the cookie is sent on every API call.
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        path="/",
    )
    # Surface the expiry to the frontend as well so it can prompt the
    # user before the cookie silently dies.
    response.headers["X-Session-Expires"] = expires_at


def _registration_allowed_or_raise(
    cfg: AuthConfig,
    invitation_token: str,
    conn,
) -> dict[str, object] | None:
    """Check the registration mode and consume invitations if needed.

    Returns:
        ``{}`` when registration is allowed open / via admin_only path
        (admin gate enforced elsewhere); a ``{"role": ...}`` dict when
        an invitation was consumed and the new user inherits that
        role; raises HTTPException otherwise.
    """
    mode = cfg.registration
    if mode == "open":
        return {"role": "user"}
    if mode == "admin_only":
        raise HTTPException(
            status_code=403,
            detail={
                "error": "registration_admin_only",
                "message": "self-registration disabled; ask the host operator to add you",
            },
        )
    # invite_only — verify and consume.
    if not invitation_token:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invitation_required",
                "message": "registration requires a valid invitation token",
            },
        )
    # We can't consume yet (need a user_id first); validate by
    # peeking — actual consume happens after user creation.
    return {"_invite_token": invitation_token}


# ---------------------------------------------------------------------------
# Registration / login / logout / me
# ---------------------------------------------------------------------------


@router.post("/register")
def register(
    req: RegisterRequest,
    response: Response,
    auth_config: AuthConfig = Depends(get_auth_config),
) -> dict[str, object]:
    """Create a new user account.

    Behaviour by ``auth.registration`` mode:

    - ``open`` — anyone can register; role defaults to ``user``.
    - ``invite_only`` — caller MUST supply ``invitation_token``; the
      role on the invitation is honoured.
    - ``admin_only`` — endpoint refuses with 403; operator uses
      ``kt admin users add`` or the admin ``POST /users`` endpoint.

    On success: row inserted, session created, cookie set, body
    carries the user dict.
    """
    if not auth_config.multi_user_enabled:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "multi_user_disabled",
                "message": "user accounts are not enabled on this host",
            },
        )

    with connection() as conn:
        verdict = _registration_allowed_or_raise(
            auth_config, req.invitation_token, conn
        )
        invite_token = (verdict or {}).get("_invite_token", "")
        invite_role: str | None = None
        if invite_token:
            # Peek-then-consume pattern: validate before creating the
            # user (so a malformed username doesn't burn a valid
            # invitation), then atomically claim once we have a user
            # id.  Two callers racing the same token: only one
            # consume() updates the row; the other returns None.
            invite = invitations_db.peek(conn, invite_token)
            if invite is None:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "invitation_invalid",
                        "message": "invitation token is invalid, expired, or already used",
                    },
                )
            invite_role = invite.role
        try:
            user = users_db.create_user(
                conn,
                req.username,
                req.password,
                role=invite_role or (verdict or {}).get("role", "user"),
                bcrypt_rounds=auth_config.bcrypt_rounds,
            )
        except users_db.UsernameInUseError as e:
            raise HTTPException(409, str(e)) from e
        except users_db.InvalidUsernameError as e:
            raise HTTPException(400, str(e)) from e

        if invite_token:
            # Atomically claim — racing register calls lose here.
            consumed = invitations_db.consume(conn, invite_token, used_by=user.id)
            if consumed is None:
                # Another caller raced and won.  Roll back our user.
                users_db.delete_user(conn, user.id)
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "invitation_race",
                        "message": "invitation was consumed by another caller; try again",
                    },
                )

        session_id, expires_at = sessions_db.create_session(
            conn, user.id, expire_hours=auth_config.session_expire_hours
        )
        users_db.touch_last_login(conn, user.id)

    _set_session_cookie(response, session_id, expires_at)
    return {"user": _user_public(user), "expires_at": expires_at}


@router.post("/login")
def login(
    req: LoginRequest,
    response: Response,
    auth_config: AuthConfig = Depends(get_auth_config),
) -> dict[str, object]:
    """Verify credentials and start a session."""
    if not auth_config.multi_user_enabled:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "multi_user_disabled",
                "message": "user accounts are not enabled on this host",
            },
        )
    with connection() as conn:
        user = users_db.verify_user_password(conn, req.username, req.password)
        if user is None:
            # Constant-time-ish — bcrypt verify cost is ~uniform per attempt.
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "invalid_credentials",
                    "message": "invalid username or password",
                },
            )
        session_id, expires_at = sessions_db.create_session(
            conn, user.id, expire_hours=auth_config.session_expire_hours
        )
        users_db.touch_last_login(conn, user.id)

    _set_session_cookie(response, session_id, expires_at)
    return {"user": _user_public(user), "expires_at": expires_at}


@router.post("/logout")
def logout(
    response: Response,
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, object]:
    """Drop the current session row + clear the cookie.  No-op when
    no cookie is present (idempotent — frontend can call on tab close
    without first checking auth state)."""
    if session_id:
        with connection() as conn:
            sessions_db.delete_session(conn, session_id)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"status": "logged_out"}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict[str, object]:
    return _user_public(user)


@router.post("/me/password")
def change_my_password(
    req: PasswordChangeRequest,
    user: User = Depends(get_current_user),
    auth_config: AuthConfig = Depends(get_auth_config),
) -> dict[str, str]:
    with connection() as conn:
        # Verify the current password before honouring the change.
        verified = users_db.verify_user_password(
            conn, user.username, req.current_password
        )
        if verified is None:
            raise HTTPException(
                status_code=401,
                detail={"error": "invalid_credentials"},
            )
        users_db.set_password(
            conn, user.id, req.new_password, bcrypt_rounds=auth_config.bcrypt_rounds
        )
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API tokens (per-user)
# ---------------------------------------------------------------------------


@router.get("/tokens")
def list_my_tokens(user: User = Depends(get_current_user)) -> dict[str, object]:
    with connection() as conn:
        toks = tokens_db.list_user_tokens(conn, user.id)
    return {
        "tokens": [
            {
                "id": t.id,
                "name": t.name,
                "last_used_at": t.last_used_at,
                "created_at": t.created_at,
            }
            for t in toks
        ]
    }


@router.post("/tokens")
def create_my_token(
    req: TokenCreateRequest, user: User = Depends(get_current_user)
) -> dict[str, object]:
    with connection() as conn:
        try:
            plaintext, token = tokens_db.create_token(conn, user.id, req.name)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    # Returned EXACTLY ONCE — the DB only stores the hash.
    return {
        "token": plaintext,
        "id": token.id,
        "name": token.name,
        "created_at": token.created_at,
    }


@router.delete("/tokens/{token_id}")
def revoke_my_token(
    token_id: int, user: User = Depends(get_current_user)
) -> dict[str, object]:
    with connection() as conn:
        deleted = tokens_db.delete_token(conn, user.id, token_id)
    if not deleted:
        raise HTTPException(404, {"error": "token_not_found"})
    return {"status": "revoked", "id": token_id}


# ---------------------------------------------------------------------------
# Admin: users
# ---------------------------------------------------------------------------


@router.get("/users")
def admin_list_users(
    user: User = Depends(get_current_user),
) -> dict[str, object]:
    _require_admin(user)
    with connection() as conn:
        all_users = users_db.list_users(conn)
    return {"users": [_user_public(u) for u in all_users]}


@router.post("/users")
def admin_create_user(
    req: AdminUserCreateRequest,
    actor: User = Depends(get_current_user),
    auth_config: AuthConfig = Depends(get_auth_config),
) -> dict[str, object]:
    _require_admin(actor)
    with connection() as conn:
        try:
            created = users_db.create_user(
                conn,
                req.username,
                req.password,
                role=req.role,
                bcrypt_rounds=auth_config.bcrypt_rounds,
            )
        except users_db.UsernameInUseError as e:
            raise HTTPException(409, str(e)) from e
        except users_db.InvalidUsernameError as e:
            raise HTTPException(400, str(e)) from e
    return {"user": _user_public(created)}


@router.patch("/users/{user_id}")
def admin_patch_user(
    user_id: int,
    req: AdminUserPatchRequest,
    actor: User = Depends(get_current_user),
) -> dict[str, object]:
    _require_admin(actor)
    with connection() as conn:
        target = users_db.get_user_by_id(conn, user_id)
        if target is None:
            raise HTTPException(404, {"error": "user_not_found"})
        # Guard: the last active admin can't demote / disable themselves.
        will_lose_admin = target.role == "admin" and (
            req.role == "user" or req.is_active is False
        )
        if will_lose_admin and users_db.count_admins(conn) <= 1:
            raise HTTPException(
                400,
                {"error": "last_admin", "message": "cannot remove last active admin"},
            )
        if req.role is not None:
            users_db.set_role(conn, user_id, req.role)
        if req.is_active is not None:
            users_db.set_active(conn, user_id, bool(req.is_active))
            if not req.is_active:
                # Nuke the user's sessions on disable.
                sessions_db.delete_user_sessions(conn, user_id)
        updated = users_db.get_user_by_id(conn, user_id)
    return {"user": _user_public(updated)}  # type: ignore[arg-type]


@router.delete("/users/{user_id}")
def admin_delete_user(
    user_id: int, actor: User = Depends(get_current_user)
) -> dict[str, object]:
    _require_admin(actor)
    with connection() as conn:
        target = users_db.get_user_by_id(conn, user_id)
        if target is None:
            raise HTTPException(404, {"error": "user_not_found"})
        if target.role == "admin" and users_db.count_admins(conn) <= 1:
            raise HTTPException(
                400,
                {"error": "last_admin", "message": "cannot delete last active admin"},
            )
        users_db.delete_user(conn, user_id)
    return {"status": "deleted", "id": user_id}


# ---------------------------------------------------------------------------
# Admin: invitations
# ---------------------------------------------------------------------------


@router.post("/invitations")
def admin_create_invitation(
    req: InvitationCreateRequest, actor: User = Depends(get_current_user)
) -> dict[str, object]:
    _require_admin(actor)
    with connection() as conn:
        plaintext, invite = invitations_db.create(
            conn,
            created_by=actor.id,
            role=req.role,
            expires_in_hours=req.expires_in_hours,
        )
    return {
        "token": plaintext,  # shown once
        "id": invite.id,
        "role": invite.role,
        "expires_at": invite.expires_at,
        "created_at": invite.created_at,
    }


@router.get("/invitations")
def admin_list_invitations(
    actor: User = Depends(get_current_user),
) -> dict[str, object]:
    _require_admin(actor)
    with connection() as conn:
        invites = invitations_db.list_unused(conn)
    return {
        "invitations": [
            {
                "id": i.id,
                "role": i.role,
                "expires_at": i.expires_at,
                "created_at": i.created_at,
                "created_by": i.created_by,
            }
            for i in invites
        ]
    }


@router.delete("/invitations/{invite_id}")
def admin_revoke_invitation(
    invite_id: int, actor: User = Depends(get_current_user)
) -> dict[str, object]:
    _require_admin(actor)
    with connection() as conn:
        ok = invitations_db.revoke(conn, invite_id)
    if not ok:
        raise HTTPException(404, {"error": "invitation_not_found_or_already_used"})
    return {"status": "revoked", "id": invite_id}


# ---------------------------------------------------------------------------
# Admin: token rotation — frontend parity with ``kt admin set-host-token`` etc.
# ---------------------------------------------------------------------------


def _rotate_token_in_config(field: str, request_app) -> str:
    """Generate a new token + write it into ``[auth]`` in config.toml.

    Mirrors the CLI :mod:`cli.admin` write path so frontend-initiated
    rotation lands in the same place.  Both paths now share
    :mod:`api.auth.config_write`, eliminating wire-format drift.

    The newly-generated value is ALSO live-installed into
    ``app.state.auth_config`` so middleware decisions on the very
    next request honour the rotation — without this, the operator
    would have to restart the server for the new token to take effect.

    When ``write_auth_section`` rejects an existing config (top-level
    scalar or nested table that the minimal writer refuses), we
    translate the ``ValueError`` into a 400 with a clear operator
    message — bubbling the bare exception would surface as an opaque
    500 in the admin UI.
    """
    new_token = secrets.token_hex(32)
    try:
        write_auth_section({field: new_token})
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "config_toml_unsupported_shape",
                "message": (
                    "config.toml contains a TOML shape the minimal "
                    "writer cannot preserve (top-level scalar or "
                    "nested table).  Move stray top-level keys into "
                    "a [section] and try again."
                ),
                "writer_error": str(e),
            },
        ) from e
    # Live-apply by replacing the frozen AuthConfig snapshot.  The
    # ``dataclasses.replace`` keeps every other field intact.
    cached = getattr(request_app.state, "auth_config", None)
    if isinstance(cached, AuthConfig):
        request_app.state.auth_config = dataclasses.replace(
            cached, **{field: new_token}
        )
    return new_token


class TokenRotateResponse(BaseModel):
    """Wire shape for token-rotate routes.

    The full plaintext token is returned ONCE so the admin UI can show
    it (and the user can copy it to their password manager).  A
    one-time-show pattern; subsequent requests can only see the
    masked-tail metadata via :func:`token_status`.
    """

    token: str
    field: str


def _mask_tail(value: str) -> str:
    if not value:
        return ""
    return value[-6:] if len(value) > 6 else value


@router.get("/admin/token-status")
def admin_token_status(
    actor: User = Depends(get_current_user),
    auth_config: AuthConfig = Depends(get_auth_config),
) -> dict[str, object]:
    """Inspect the configured host_token / admin_token without leaking them.

    Returns enabled flags + last-6-chars tail of each so the admin UI
    can show "current host token: ...abc123" without revealing the
    full secret.
    """
    _require_admin(actor)
    return {
        "host_token": {
            "enabled": auth_config.host_token_enabled,
            "tail": _mask_tail(auth_config.host_token),
        },
        "admin_token": {
            "enabled": auth_config.admin_token_enabled,
            "tail": _mask_tail(auth_config.admin_token),
        },
    }


@router.post("/admin/rotate-host-token", response_model=TokenRotateResponse)
def admin_rotate_host_token(
    request: "Request",  # noqa: F821 — fastapi resolves the actual type
    actor: User = Depends(get_current_user),
) -> dict[str, str]:
    """Generate + save a new ``host_token``.  Admin-only.

    Existing connections continue to use the old token until they
    reconnect — there is no kick-everyone-out semantic baked into the
    middleware (it compares against the live ``app.state.auth_config``
    on every request, so the next request from an existing client will
    401).  The admin UI is expected to surface this and prompt the
    operator before clicking the button.
    """
    _require_admin(actor)
    new_token = _rotate_token_in_config("host_token", request.app)
    logger.info("auth: host_token rotated via API by admin")
    return {"token": new_token, "field": "host_token"}


@router.post("/admin/rotate-admin-token", response_model=TokenRotateResponse)
def admin_rotate_admin_token(
    request: "Request",  # noqa: F821
    actor: User = Depends(get_current_user),
) -> dict[str, str]:
    """Generate + save a new ``admin_token``.  Admin-only.

    The rotation is live: the next request must carry the new token
    in the ``X-Admin-Token`` header to pass L3.  The frontend MUST
    update its stored admin token before any subsequent config-mutating
    call or 401 storms ensue.
    """
    _require_admin(actor)
    new_token = _rotate_token_in_config("admin_token", request.app)
    logger.info("auth: admin_token rotated via API by admin")
    return {"token": new_token, "field": "admin_token"}


__all__ = ["router"]
