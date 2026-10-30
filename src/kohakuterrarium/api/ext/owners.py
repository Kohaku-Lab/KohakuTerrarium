"""Resolve a UI owner reference to a live instance, and fan out its pushes.

``package:<pkg>`` resolves to the package's process-wide ``ui.handler``
instance. ``plugin:`` / ``tool:`` / ``trigger:`` owners resolve inside one
creature: the creature must belong to the requested session on the
request's own service, which is per-user under multi-user mode.
"""

import asyncio
import inspect
import threading
from dataclasses import dataclass
from functools import partial
from typing import Any

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.ext.policy import slot_enabled
from kohakuterrarium.api.ext.refs import import_package_ref
from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.errors import InvalidRequestError, KTError
from kohakuterrarium.modules.ui_owner import (
    OwnerHasNoUIError,
    OwnerNotLoadedError,
    accepts_context,
    bind_publisher,
    has_ui,
    parse_owner,
    resolve_runtime_owner,
)
from kohakuterrarium.packages.slots.ui import (
    UISlot,
    get_package_ui,
    packages_declaring,
)
from kohakuterrarium.terrarium import LocalTerrariumService
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class OwnerUnsupportedError(InvalidRequestError):
    """The owner lives where this host cannot reach it (e.g. another node)."""


class OwnerDisabledError(OwnerNotLoadedError):
    """The package contributing this owner's UI is disabled on this host."""


class OwnerNotMultiUserError(KTError):
    """A shared package owner that cannot tell users apart, on a multi-user host."""


@dataclass(frozen=True)
class ResolvedOwner:
    """A live owner instance plus the push key its subscribers listen on."""

    owner: str
    kind: str
    name: str
    instance: Any
    key: tuple
    creature_id: str = ""


@dataclass(frozen=True)
class _Subscriber:
    loop: asyncio.AbstractEventLoop
    deliver: Any
    user_id: int | None
    multi_user: bool


class OwnerEventBus:
    """Process-wide fan-out from ``ui_emit`` to WebSocket subscribers.

    Package keys are shared by every user of the host: a push targeted with
    ``user_id`` reaches only that user's subscribers, and an untargeted push
    reaches only single-user subscribers. Runtime keys already belong to one
    user's engine, so their pushes reach every subscriber of the key.
    """

    def __init__(self) -> None:
        self._subs: dict[tuple, dict[int, _Subscriber]] = {}
        self._lock = threading.Lock()
        self._next = 0

    def subscribe(
        self,
        key: tuple,
        deliver: Any,
        *,
        user_id: int | None = None,
        multi_user: bool = False,
    ) -> int:
        """Register ``deliver(event, data)`` on ``key``; runs on the caller's loop."""
        sub = _Subscriber(asyncio.get_running_loop(), deliver, user_id, multi_user)
        with self._lock:
            self._next += 1
            self._subs.setdefault(key, {})[self._next] = sub
            return self._next

    def unsubscribe(self, key: tuple, token: int) -> None:
        with self._lock:
            subs = self._subs.get(key)
            if subs is not None:
                subs.pop(token, None)
                if not subs:
                    self._subs.pop(key, None)

    def subscriber_count(self, key: tuple) -> int:
        with self._lock:
            return len(self._subs.get(key, {}))

    def publish(
        self, key: tuple, event: str, data: Any = None, user_id: int | None = None
    ) -> None:
        """Deliver to the matching subscribers of ``key``; safe from any thread."""
        with self._lock:
            targets = list(self._subs.get(key, {}).values())
        shared = bool(key) and key[0] == "package"
        for sub in targets:
            if shared:
                if user_id is not None and sub.user_id != user_id:
                    continue
                if user_id is None and sub.multi_user:
                    continue
            try:
                sub.loop.call_soon_threadsafe(sub.deliver, str(event), data)
            except RuntimeError:
                continue


BUS = OwnerEventBus()


async def close_handler(instance: Any) -> None:
    """Run a replaced handler's optional ``aclose()`` or ``close()`` hook."""
    for name in ("aclose", "close"):
        hook = getattr(instance, name, None)
        if callable(hook):
            try:
                result = hook()
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                logger.warning("UI handler close hook failed", hook=name, error=str(e))
            return


class PackageHandlers:
    """Process-wide ``ui.handler`` instances, one per package.

    An instance is rebuilt (and the old one closed) only when that
    package's own fingerprint changes.
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[tuple, Any]] = {}
        self._lock = threading.Lock()

    async def get(self, slot: UISlot) -> Any:
        with self._lock:
            cached = self._cache.get(slot.package)
            if cached is not None and cached[0] == slot.fingerprint:
                return cached[1]
            handler_cls = import_package_ref(
                slot.package, slot.handler, reload=cached is not None
            )
            instance = handler_cls()
            self._cache[slot.package] = (slot.fingerprint, instance)
        if cached is not None:
            await close_handler(cached[1])
        return instance


HANDLERS = PackageHandlers()


def _realm(service: Any) -> int:
    """Identity of the engine a runtime owner lives in (one per L4 user)."""
    return id(service.engine)


def _check_contributor_enabled(kind: str, name: str, auth_config: AuthConfig) -> None:
    """Refuse a runtime owner whose declaring package has its UI disabled."""
    for slot in packages_declaring(kind, name):
        if not slot_enabled(slot, auth_config):
            raise OwnerDisabledError(
                f"{kind}:{name} belongs to package {slot.package}, whose UI is disabled"
            )


def _check_multi_user_safe(instance: Any, owner: str, auth_config: AuthConfig) -> None:
    """Under multi-user mode a shared owner must accept ``context`` everywhere."""
    if not auth_config.multi_user_enabled:
        return
    for name in ("ui_state", "ui_call"):
        method = getattr(instance, name, None)
        if callable(method) and not accepts_context(method):
            raise OwnerNotMultiUserError(
                f"{owner}.{name} does not accept context=, so it cannot tell "
                "users apart on a multi-user host"
            )


async def resolve_owner(
    service: Any,
    auth_config: AuthConfig,
    owner: str,
    session_id: str = "",
    creature_id: str = "",
) -> ResolvedOwner:
    """Find the live owner and bind its ``ui_emit`` to :data:`BUS`."""
    kind, name = parse_owner(owner)
    if kind == "package":
        slot = get_package_ui(name)
        if slot is None or not slot_enabled(slot, auth_config):
            raise OwnerNotLoadedError(f"package:{name} has no enabled UI")
        if not slot.handler:
            raise OwnerHasNoUIError(f"package:{name} declares no ui.handler")
        instance = await HANDLERS.get(slot)
        _check_multi_user_safe(instance, owner, auth_config)
        resolved = ResolvedOwner(owner, kind, name, instance, ("package", name))
    else:
        if not session_id or not creature_id:
            raise InvalidRequestError(f"{owner} needs session_id and creature_id")
        if not isinstance(service, LocalTerrariumService):
            raise OwnerUnsupportedError(f"{owner} is not reachable on this host")
        _check_contributor_enabled(kind, name, auth_config)
        cid = await resolve_creature_id(service, creature_id, session_id)
        agent = service.engine.get_creature(cid).agent
        instance = resolve_runtime_owner(agent, kind, name)
        key = (_realm(service), kind, name, cid)
        resolved = ResolvedOwner(owner, kind, name, instance, key, cid)
    if not has_ui(resolved.instance):
        raise OwnerHasNoUIError(f"{owner} exposes no UI interface")
    bind_publisher(resolved.instance, partial(BUS.publish, resolved.key))
    return resolved
