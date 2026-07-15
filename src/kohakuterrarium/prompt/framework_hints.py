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
with `run_in_background=true`. Background results arrive automatically in a later turn.

**Critical rule**: Once you delegate a task to background, do NOT do the same work yourself.
The work is already being done — doing it again wastes tokens and produces duplicates.

**Workflow example**:
1. Dispatch `explore` to investigate the project structure (background)
2. Dispatch `research` to look up API documentation (background)
3. **Stop your response and wait** — both are working in parallel
4. Results arrive automatically → synthesize and continue

**Direct sub-agents** (set `run_in_background=false`):
Use when you need the result before continuing and the task is short.

**WRONG** (duplicate work):
1. Dispatch `explore` to investigate the codebase ← background
2. Start reading the same codebase yourself ← WRONG, same task!

IMPORTANT: When calling a function, output ONLY the function call block. Do not output any extra text, markers, or filler characters (like dashes, dots, etc.) before or after the function call. If you need results before continuing, end with the function call and nothing else.
IMPORTANT: You may ONLY call functions listed in the "Available Functions" section above. Do NOT call functions that are not listed.
"""

_DEFAULT_EXECUTION_MODEL_STATIC = """
## Execution Model

- **Direct tools**: Results return after you finish your response
- **Sub-agents**: Run in background by default (set `run_in_background=false` to wait)

### Background Tasks

Sub-agents run in background by default. Background results arrive automatically
in a later turn.

**Critical rule**: Once you delegate a task to background, do NOT do the same
work yourself. The work is already being done.

**Workflow**: dispatch sub-agents → do DIFFERENT direct work or stop and wait →
results arrive automatically → continue.

**Direct sub-agents**: Set `run_in_background=false` when you need the result
before continuing and the task is short.

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

When a task runs in background, the result arrives automatically in
a later turn. You do NOT need to poll or wait.

**Critical**: Once you delegate work to background, do NOT do the same
work yourself. The task is already being done.

**Example workflow**:
1. Dispatch `explore` sub-agent to investigate module A (background)
2. Use `read` on a DIFFERENT file (module B) yourself (direct)
3. Stop — explore result for module A arrives in next turn

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
