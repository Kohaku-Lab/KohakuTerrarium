"""ASGI dispatcher for ``/api/ext/<package>/...``.

Serves a package's UI asset files and forwards every other path to a
sub-application built from the package's ``ui.api`` router (the raw-route
fallback; owner RPC is the primary channel). Sub-apps are rebuilt when the
installed-package set changes, so installs, removals and manifest edits
take effect without remounting anything.
"""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, FastAPI
from starlette.requests import HTTPConnection
from starlette.responses import FileResponse, JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from kohakuterrarium.api.auth.asset_token import verify_asset_token
from kohakuterrarium.api.auth.config import AuthConfig, load_auth_config
from kohakuterrarium.api.auth.dependencies import get_optional_user
from kohakuterrarium.api.ext import EXT_PACKAGE_SCOPE_KEY
from kohakuterrarium.api.ext.policy import slot_enabled
from kohakuterrarium.api.ext.refs import import_package_ref
from kohakuterrarium.packages.slots.ui import UISlot, get_package_ui, resolve_ui_asset
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_MEDIA_TYPES: dict[str, str] = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".html": "text/html",
}


def _route_path(scope: Scope) -> str:
    """The request path below the mount point (Starlette keeps the full path)."""
    path: str = scope.get("path", "")
    root = scope.get("root_path", "")
    if root and path.startswith(root):
        return path[len(root) :] or "/"
    return path


class ExtensionDispatcher:
    """Route one request to package UI assets or the package's API sub-app."""

    def __init__(
        self,
        parent: Any,
        configure_subapp: Callable[[FastAPI], None] | None = None,
    ) -> None:
        self._parent = parent
        self._configure = configure_subapp
        self._apps: dict[str, tuple[tuple, ASGIApp | None, str]] = {}

    def _auth_config(self) -> AuthConfig:
        cached = getattr(self._parent.state, "auth_config", None)
        return cached if isinstance(cached, AuthConfig) else load_auth_config()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            return
        rel = _route_path(scope).lstrip("/")
        pkg, _, rest = rel.partition("/")
        slot = get_package_ui(pkg) if pkg else None
        if slot is None:
            await _error(scope, receive, send, 404, "extension_not_found", pkg)
            return
        if not slot_enabled(slot, self._auth_config()):
            await _error(scope, receive, send, 404, "extension_disabled", pkg)
            return
        head, _, tail = rest.partition("/")
        if head == "ui":
            await self._serve_asset(scope, receive, send, slot, tail)
            return
        if not await self._user_allowed(scope):
            await _error(scope, receive, send, 401, "auth_required", "")
            return
        app, error = self._sub_app(slot)
        if app is None:
            await _error(
                scope, receive, send, 500 if error else 404, "extension_api", error
            )
            return
        child = dict(scope)
        child["root_path"] = scope.get("root_path", "") + "/" + pkg
        child[EXT_PACKAGE_SCOPE_KEY] = slot.package
        await app(child, receive, send)

    async def _serve_asset(
        self, scope: Scope, receive: Receive, send: Send, slot: UISlot, tail: str
    ) -> None:
        method = scope.get("method", "").upper()
        if scope["type"] != "http" or method not in ("GET", "HEAD"):
            await _error(scope, receive, send, 405, "method_not_allowed", "")
            return
        token, _, file_path = tail.partition("/")
        secret = getattr(self._parent.state, "ui_asset_secret", None)
        if not isinstance(secret, bytes) or not verify_asset_token(
            secret, token, slot.package
        ):
            await _error(scope, receive, send, 403, "asset_token_invalid", "")
            return
        target = resolve_ui_asset(slot, file_path)
        if target is None:
            await _error(scope, receive, send, 404, "asset_not_found", file_path)
            return
        response = FileResponse(
            str(target),
            media_type=_MEDIA_TYPES.get(target.suffix.lower()),
            headers={"Cache-Control": "private, max-age=300"},
        )
        await response(scope, receive, send)

    async def _user_allowed(self, scope: Scope) -> bool:
        """Raw routes need a signed-in user when the host requires accounts."""
        cfg = self._auth_config()
        if not (cfg.multi_user_enabled and cfg.multi_user == "required"):
            return True
        conn = HTTPConnection({**scope, "app": self._parent})
        return get_optional_user(conn, conn.headers.get("authorization")) is not None

    def _sub_app(self, slot: UISlot) -> tuple[ASGIApp | None, str]:
        """The cached sub-app, rebuilt only when this package's fingerprint changes."""
        if not slot.api:
            return None, ""
        cached = self._apps.get(slot.package)
        if cached is not None and cached[0] == slot.fingerprint:
            return cached[1], cached[2]
        app, error = self._build(slot, reload=cached is not None)
        self._apps[slot.package] = (slot.fingerprint, app, error)
        return app, error

    def _build(self, slot: UISlot, *, reload: bool) -> tuple[ASGIApp | None, str]:
        try:
            target = import_package_ref(slot.package, slot.api, reload=reload)
        except Exception as e:
            logger.error(
                "Extension API failed to load",
                package=slot.package,
                api=slot.api,
                error=str(e),
                exc_info=True,
            )
            return None, f"{type(e).__name__}: {e}"
        if isinstance(target, APIRouter):
            app = FastAPI(title=f"{slot.package} extension")
            app.include_router(target)
        elif isinstance(target, FastAPI):
            app = target
        else:
            return None, f"{slot.api} is not an APIRouter or FastAPI app"
        app.state = self._parent.state
        if self._configure is not None:
            self._configure(app)
        return app, ""


async def _error(
    scope: Scope, receive: Receive, send: Send, status: int, code: str, detail: str
) -> None:
    """Reject with JSON over HTTP, or close a WebSocket handshake with 4000+status."""
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 4000 + status, "reason": code})
        return
    response = JSONResponse(
        status_code=status, content={"error": code, "detail": detail}
    )
    await response(scope, receive, send)
