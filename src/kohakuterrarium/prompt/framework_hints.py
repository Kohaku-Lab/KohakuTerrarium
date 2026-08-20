"""Provide canonical, overrideable framework-hint prose blocks.

Creature overrides take precedence over package overrides and built-in defaults.
An empty override omits the block; unknown keys are ignored with a warning.
"""

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


HINT_OUTPUT_MODEL = "framework.output_model"
HINT_EXECUTION_MODEL_DYNAMIC = "framework.execution_model.dynamic"
HINT_EXECUTION_MODEL_STATIC = "framework.execution_model.static"
HINT_EXECUTION_MODEL_NATIVE = "framework.execution_model.native"


# Only the default output block supports named-output interpolation.
_DEFAULT_OUTPUT_MODEL = """
## Output Format

Plain text = internal thinking (not sent anywhere)
To send output externally, you MUST wrap in output block:

[/output_<name>]your content here[output_<name>/]
{named_outputs_section}
"""

_DEFAULT_EXECUTION_MODEL_DYNAMIC = """
## Execution Model

- **Direct tools**: Results return after you finish your response
- **Sub-agents**: Run in background by default (set `run_in_background=false` to wait for result)
- **Commands** (info, jobs, wait): Execute during your response

### Background Tasks

Sub-agents run in background by default. Tools can also run in background
with `run_in_background=true`.

Starting a background task does not immediately return a result or give you
another turn to act. You are normally invoked again after the task finishes or
fails. Before ending your response, start every independent background task you
already know is needed.

Do not poll, sleep, restart, or duplicate background work. If you know before
starting that your next step requires the result, use `run_in_background=false`
and wait for it.

A server, watcher, or daemon may run indefinitely and never produce a completion
result. If you need to interact with a long-running process afterward, use a
startup action that can finish while the process keeps running. This gives you a
result to continue from without waiting for the long-running process to exit.

**Workflow example**:
1. Start all independent background investigations in the same response
2. End your response if no other independent work can be done now
3. Continue after their results are delivered

**WRONG** (duplicate work):
1. Dispatch `explore` to investigate the codebase in the background
2. Start the same investigation yourself

IMPORTANT: When calling a function, output ONLY the function call block. Do not output any extra text, markers, or filler characters (like dashes, dots, etc.) before or after the function call. If you need results before continuing, end with the function call and nothing else.
IMPORTANT: You may ONLY call functions listed in the "Available Functions" section above. Do NOT call functions that are not listed.
"""

_DEFAULT_EXECUTION_MODEL_STATIC = """
## Execution Model

- **Direct tools**: Results return after you finish your response
- **Sub-agents**: Run in background by default (set `run_in_background=false` to wait)

### Background Tasks

Sub-agents run in background by default.

Starting a background task does not immediately return a result or give you
another turn to act. You are normally invoked again after the task finishes or
fails. Before ending your response, start every independent background task you
already know is needed.

Do not poll, sleep, restart, or duplicate background work. If you know before
starting that your next step requires the result, set `run_in_background=false`
and wait for it.

A server, watcher, or daemon may run indefinitely and never produce a completion
result. If you need to interact with a long-running process afterward, use a
startup action that can finish while the process keeps running. This gives you a
result to continue from without waiting for the long-running process to exit.

IMPORTANT: When calling a function, output ONLY the function call block. Do not output any extra text, markers, or filler characters before or after. If you need results before continuing, end with the function call and nothing else.
IMPORTANT: You may ONLY call functions listed in the "Available Functions" section above. Do NOT call functions that are not listed.
"""

_DEFAULT_EXECUTION_MODEL_NATIVE = """## Tool Usage

Tools are called via the API's native function calling mechanism.
You do not need to format tool calls manually.

By default, tool results are returned immediately after your response.
You WILL receive the result before your next turn.

### Background Execution

Sub-agents run in background by default. Set `run_in_background=false`
to wait for a sub-agent's result before continuing (use for short tasks).
Tools can also run in background with `run_in_background=true`.

Starting a background task does not immediately return a result or give you
another turn to act. You are normally invoked again after the task finishes or
fails. Before ending your response, start every independent background task you
already know is needed.

Do not poll, sleep, restart, or duplicate background work. If you know before
starting that your next step requires the result, set `run_in_background=false`
and wait for it.

A server, watcher, or daemon may run indefinitely and never produce a completion
result. If you need to interact with a long-running process afterward, use a
startup action that can finish while the process keeps running. This gives you a
result to continue from without waiting for the long-running process to exit.

**Example workflow**:
1. Start all independent background investigations in the same response
2. End your response if no other independent work can be done now
3. Continue after their results are delivered

You may ONLY call tools listed in the "Available Functions" section above.
"""


_DEFAULTS: dict[str, str] = {
    HINT_OUTPUT_MODEL: _DEFAULT_OUTPUT_MODEL,
    HINT_EXECUTION_MODEL_DYNAMIC: _DEFAULT_EXECUTION_MODEL_DYNAMIC,
    HINT_EXECUTION_MODEL_STATIC: _DEFAULT_EXECUTION_MODEL_STATIC,
    HINT_EXECUTION_MODEL_NATIVE: _DEFAULT_EXECUTION_MODEL_NATIVE,
}


def canonical_keys() -> tuple[str, ...]:
    """Return recognized override keys in definition order."""
    return tuple(_DEFAULTS.keys())


def get_framework_hint(
    key: str,
    overrides: dict[str, str] | None = None,
) -> str | None:
    """Resolve a canonical hint, preserving empty-string suppression.

    Unknown requested keys return ``None``; unknown override keys are ignored.
    """
    if key not in _DEFAULTS:
        logger.warning("Unknown framework-hint key requested", hint_key=key)
        return None

    if overrides:
        _warn_unknown_overrides(overrides)
        if key in overrides:
            logger.debug("Framework-hint override applied", hint_key=key)
            return overrides[key]

    return _DEFAULTS[key]


def _warn_unknown_overrides(overrides: dict[str, str]) -> None:
    """Warn when an override map contains non-canonical keys."""
    unknown = [k for k in overrides if k not in _DEFAULTS]
    if unknown:
        logger.warning(
            "Unknown framework-hint override keys ignored",
            unknown_keys=sorted(unknown),
            valid_keys=sorted(_DEFAULTS.keys()),
        )


def merge_overrides(
    package_level: dict[str, str] | None,
    creature_level: dict[str, str] | None,
) -> dict[str, str]:
    """Merge override maps with creature entries taking precedence.

    Unknown keys remain present so lookup can report them consistently.
    """
    merged: dict[str, str] = {}
    if package_level:
        merged.update(package_level)
    if creature_level:
        merged.update(creature_level)
    return merged
