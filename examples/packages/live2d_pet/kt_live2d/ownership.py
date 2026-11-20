"""One UI controller per engine, independent of optional agent expression."""

import asyncio
import uuid
import weakref
from dataclasses import dataclass

from .bindings import remove_binding


@dataclass
class Controller:
    client: str
    token: str
    expiry: asyncio.TimerHandle | None = None


controllers = weakref.WeakKeyDictionary()


def release_controller(engine, token):
    current = controllers.get(engine)
    if current is None or current.token != token:
        return
    controllers.pop(engine)
    if current.expiry:
        current.expiry.cancel()
    remove_binding(engine)


def controller_view(engine):
    current = controllers.get(engine)
    return {"client": current.client, "token": current.token} if current else None


def claim_controller(engine, client, operation, token=""):
    current = controllers.get(engine)
    owns = current and current.client == client and current.token == token
    if operation == "release":
        if owns:
            release_controller(engine, token)
        return controller_view(engine)
    if operation == "renew" and not owns:
        raise ValueError("Controller expired or changed.")
    if operation == "claim" and current and not owns:
        raise ValueError("Another page controls the pet.")
    if operation == "takeover" and (current.token if current else "") != token:
        raise ValueError("Controller changed; retry takeover.")
    if not owns:
        if current:
            release_controller(engine, current.token)
        current = Controller(client, uuid.uuid4().hex)
        controllers[engine] = current
    if current.expiry:
        current.expiry.cancel()
    current.expiry = asyncio.get_running_loop().call_later(
        20, release_controller, engine, current.token
    )
    return controller_view(engine)
