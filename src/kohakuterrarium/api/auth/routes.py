"""Expose authentication, user, token, invitation, and admin endpoints.

Public capabilities reveal no secrets. Registration follows host policy, user routes
require an authenticated account, and administrative routes require the admin role.
Generated bearer and invitation plaintexts are returned only at creation time.
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


# Public authentication-policy discovery.


@router.get("/capabilities")
def capabilities(
    auth_config: AuthConfig = Depends(get_auth_config),
) -> dict[str, object]:
    """Advertise non-secret authentication requirements before login."""
    return {
        "schema": _CAPABILITIES_SCHEMA,
        "auth": auth_config.as_capabilities_dict(),
    }


# Request and response payloads.


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=1)
    invitation_token: str = Field(
        default="", description="Required in invite_only mode"
    )
    client_kind: Literal["browser", "api"] = Field(
        default="browser",
        description=(
            "``browser`` (default) sets a session cookie only — fine for "
            "same-origin web frontends.  ``api`` ALSO mints a long-lived "
            "API token and returns its plaintext in the response body so "
            "cross-origin frontends, CLIs, and bundled apps can carry the "
            "credential in ``Authorization: Bearer ...`` without relying "
            "on cookies (which CORS-without-credentials blocks)."
        ),
    )


class LoginRequest(BaseModel):
    username: str
    password: str
    client_kind: Literal["browser", "api"] = Field(
        default="browser",
        description=(
            "Same semantics as on ``RegisterRequest`` — ``api`` returns "
            "an additional plaintext bearer token alongside the cookie."
        ),
    )


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


# Authorization, serialization, and credential helpers.


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
    """Set an HttpOnly, SameSite-Lax session cookie and expose its expiry.

    ``Secure`` is not forced because TLS may terminate at a reverse proxy and local
    desktop deployments may use loopback HTTP.
    """
    # The root path makes the session available to every API namespace.
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        path="/",
    )
    # Explicit expiry metadata lets clients warn before the browser drops the cookie.
    response.headers["X-Session-Expires"] = expires_at


# Auto-minted client tokens remain distinguishable from user-named credentials.
_AUTO_API_TOKEN_NAME = "auto:web-login"


def _maybe_mint_api_token(
    conn,
    user: User,
    client_kind: str,
) -> str:
    """Mint a one-time plaintext bearer only for API-style clients.

    Existing rows cannot be reused because their plaintext is never stored. Browser
    clients rely on the same-origin session cookie instead.
    """
    if client_kind != "api":
        return ""
    plaintext, _ = tokens_db.create_token(conn, user.id, _AUTO_API_TOKEN_NAME)
    return plaintext


def _registration_allowed_or_raise(
    cfg: AuthConfig,
    invitation_token: str,
    conn,
) -> dict[str, object] | None:
    """Enforce self-registration policy and retain invite context for claiming.

    Open registration assigns the user role, admin-only rejects self-service, and
    invite-only defers the atomic claim until a new user ID exists.
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
    # Invite-only registration requires a token that can later be claimed.
    if not invitation_token:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invitation_required",
                "message": "registration requires a valid invitation token",
            },
        )
    # Claiming must wait until the created user provides ``used_by``.
    return {"_invite_token": invitation_token}


# Account registration and session lifecycle.


@router.post("/register")
def register(
    req: RegisterRequest,
    response: Response,
    auth_config: AuthConfig = Depends(get_auth_config),
) -> dict[str, object]:
    """Create an account under registration policy and start its first session."""
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
            # Validate before user creation so invalid account data does not consume
            # the invitation; the later conditional claim resolves concurrent races.
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
            # Only one concurrent registration can satisfy the claim predicate.
            consumed = invitations_db.consume(conn, invite_token, used_by=user.id)
            if consumed is None:
                # Remove the unentitled account when another caller won the invitation.
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
        bearer = _maybe_mint_api_token(conn, user, req.client_kind)

    _set_session_cookie(response, session_id, expires_at)
    payload: dict[str, object] = {"user": _user_public(user), "expires_at": expires_at}
    if bearer:
        payload["token"] = bearer
    return payload


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
            # Return one generic credential failure so username validity is not revealed.
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
        bearer = _maybe_mint_api_token(conn, user, req.client_kind)

    _set_session_cookie(response, session_id, expires_at)
    payload: dict[str, object] = {"user": _user_public(user), "expires_at": expires_at}
    if bearer:
        payload["token"] = bearer
    return payload


@router.post("/logout")
def logout(
    response: Response,
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, object]:
    """Delete the current session and clear the cookie idempotently."""
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
        # Possession of the current credential is required before replacement.
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


# Per-user API token management.


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
    # Plaintext is returned only here because persistence stores only its digest.
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


# Administrative user management.


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
        # At least one active administrator must remain able to manage the host.
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
                # Disabled users must lose all existing session access immediately.
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


# Administrative invitation management.


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
        "token": plaintext,  # The digest is the only persisted representation.
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


# Administrative host and admin token rotation.


def _rotate_token_in_config(field: str, request_app) -> str:
    """Persist a generated token and replace the live auth snapshot.

    CLI and API rotation share one writer. Unsupported TOML shapes become an explicit
    client error, and successful rotation affects the next middleware decision without
    requiring process restart.
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
    # Replace only the rotated field while preserving the immutable policy snapshot.
    cached = getattr(request_app.state, "auth_config", None)
    if isinstance(cached, AuthConfig):
        request_app.state.auth_config = dataclasses.replace(
            cached, **{field: new_token}
        )
    return new_token


class TokenRotateResponse(BaseModel):
    """Return rotated plaintext once; later status responses expose only its tail."""

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
    """Return enabled flags and short token tails without exposing full secrets."""
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
    """Rotate the host token and require it on every subsequent request."""
    _require_admin(actor)
    new_token = _rotate_token_in_config("host_token", request.app)
    logger.info("auth: host_token rotated via API by admin")
    return {"token": new_token, "field": "host_token"}


@router.post("/admin/rotate-admin-token", response_model=TokenRotateResponse)
def admin_rotate_admin_token(
    request: "Request",  # noqa: F821
    actor: User = Depends(get_current_user),
) -> dict[str, str]:
    """Rotate the admin token and require it for subsequent mutations."""
    _require_admin(actor)
    new_token = _rotate_token_in_config("admin_token", request.app)
    logger.info("auth: admin_token rotated via API by admin")
    return {"token": new_token, "field": "admin_token"}


__all__ = ["router"]
