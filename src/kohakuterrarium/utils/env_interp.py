"""Environment-variable interpolation for configuration values.

Expands ``${VAR}`` and ``${VAR:default}`` markers inside strings (and,
recursively, inside dict/list structures) against ``os.environ``. The
lookup happens at call time, so re-running the loader after the process
mutates its own environment picks up the new values — there is no
import-time snapshot to go stale.

This lives in :mod:`utils` (the shared bottom layer) so both the agent
config loader (:mod:`core.config`) and the LLM provider/profile loader
(:mod:`llm.backends`) can apply the exact same semantics without an
``llm -> core`` dependency.
"""

import os
import re
from typing import Any

# ``${VAR}`` or ``${VAR:default}``. The var name excludes ``}`` and ``:``;
# the optional default (after ``:``) excludes ``}``.
ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")


def interpolate_env_vars(value: Any) -> Any:
    """Recursively interpolate ``${VAR}`` / ``${VAR:default}`` in ``value``.

    Strings are expanded; dicts and lists are walked and rebuilt; every
    other type is returned unchanged. An undefined variable with no
    default collapses to the empty string (matching the historical
    agent-config behaviour).
    """
    if isinstance(value, str):

        def replace_env(match: re.Match) -> str:
            var_name = match.group(1)
            default = match.group(2)
            return os.environ.get(var_name, default if default is not None else "")

        return ENV_VAR_PATTERN.sub(replace_env, value)
    if isinstance(value, dict):
        return {k: interpolate_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate_env_vars(v) for v in value]
    return value
