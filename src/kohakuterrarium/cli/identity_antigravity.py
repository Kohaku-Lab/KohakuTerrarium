"""CLI access to the host's existing agy consumer account."""

import asyncio
import json

from kohakuterrarium.llm.antigravity_auth import AntigravityError
from kohakuterrarium.studio.identity.antigravity import (
    get_models,
    get_status,
    refresh_credentials,
)


def login_cli() -> int:
    status = get_status()
    print("Antigravity uses the official agy login on this Windows host.")
    print("Run agy to sign in, then use kt config antigravity status.")
    print(json.dumps(status))
    return 0 if status["state"] == "ready" else 1


def run_cli(action: str) -> int:
    try:
        if action == "status":
            result = get_status()
        elif action == "refresh":
            result = asyncio.run(refresh_credentials())
        elif action == "models":
            result = asyncio.run(get_models())
        else:
            raise ValueError("Unknown Antigravity action")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except AntigravityError as exc:
        print(str(exc))
        return 1
