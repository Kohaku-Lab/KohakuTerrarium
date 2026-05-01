"""``/env`` slash command — read-only view of the agent's environment.

Same redaction logic as :func:`creature_state._redacted_env`: we strip
keys whose name (case-insensitive) contains any of the standard
credential substrings (``key``, ``token``, ``secret``, ``password``,
``credential``).
"""

import os
from typing import Any

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)

_CREDENTIAL_SUBSTRINGS = (
    "key",
    "token",
    "secret",
    "password",
    "credential",
)


@register_user_command("env")
class EnvCommand(BaseUserCommand):
    name = "env"
    aliases = ["environment"]
    description = (
        "List process environment variables (credential keys redacted). "
        "Optional substring filter: /env [<filter>]"
    )
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        agent = context.agent
        if agent is None:
            return UserCommandResult(error="No agent context.")
        needle = (args or "").strip().lower()
        env = _redacted_env()
        if needle:
            env = {k: v for k, v in env.items() if needle in k.lower()}
        if not env:
            return UserCommandResult(
                output=(
                    "No matching variables."
                    if needle
                    else "No environment variables visible."
                )
            )
        ws = getattr(agent, "workspace", None)
        pwd = ws.get() if ws else os.getcwd()
        lines = [f"Working directory: {pwd}", f"Environment ({len(env)} vars):"]
        for k in sorted(env):
            lines.append(f"  {k}={env[k]}")
        return UserCommandResult(output="\n".join(lines))


def _redacted_env() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in os.environ.items():
        lk = k.lower()
        if any(sub in lk for sub in _CREDENTIAL_SUBSTRINGS):
            continue
        out[k] = v
    return out
