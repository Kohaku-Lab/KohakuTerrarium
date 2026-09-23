"""Host-local Antigravity status and explicit account model discovery."""

import asyncio

from kohakuterrarium.llm.antigravity_auth import AgyCredentials
from kohakuterrarium.llm.antigravity_client import discover_models


def get_status() -> dict:
    return {**AgyCredentials.status(), "local_only": True, "credential_owner": "agy"}


async def refresh_credentials() -> dict:
    await AgyCredentials.ensure_fresh()
    return await asyncio.to_thread(get_status)


async def get_models() -> dict:
    return {"models": await discover_models()}
