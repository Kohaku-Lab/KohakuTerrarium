"""
Framework-hint prose blocks used by the prompt aggregator.

The aggregator splices four short prose blocks into every creature's system
prompt. Each block has a stable, dotted **canonical key** so harness authors
can override the wording without patching the framework.

Canonical keys
--------------

=============================================  ===========================================
Key                                            Meaning
=============================================  ===========================================
``framework.output_model``                     Output-block wrapping rules + named outputs
``framework.execution_model.dynamic``          Execution model for dynamic skill mode
``framework.execution_model.static``           Execution model for static skill mode
``framework.execution_model.native``           Execution model for native tool calling
=============================================  ===========================================

Override resolution order (first match wins, built-in is the fallback):

1. Creature-level ``framework_hint_overrides`` (``AgentConfig``)
2. Package-level ``framework_hints`` map in ``kohaku.yaml``
3. Built-in defaults defined in this module

An empty string override means "omit this block entirely". Unknown keys are
ignored and logged at WARNING level.
"""

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Canonical key constants — use these, not string literals, when looking up
# hints from the aggregator.
# ---------------------------------------------------------------------------
HINT_OUTPUT_MODEL = "framework.output_model"
HINT_EXECUTION_MODEL_DYNAMIC = "framework.execution_model.dynamic"
HINT_EXECUTION_MODEL_STATIC = "framework.execution_model.static"
HINT_EXECUTION_MODEL_NATIVE = "framework.execution_model.native"


# ---------------------------------------------------------------------------
# Default prose blocks.
# ``framework.output_model`` retains the ``{named_outputs_section}`` placeholder
# because that part is populated at aggregation time from the registered
# named outputs. Override prose does NOT need the placeholder — if an override
# is supplied, it is used verbatim (no ``.format(...)`` is applied).
# ---------------------------------------------------------------------------
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


# Map canonical key -> default prose. Single source of truth.
_DEFAULTS: dict[str, str] = {
    HINT_OUTPUT_MODEL: _DEFAULT_OUTPUT_MODEL,
    HINT_EXECUTION_MODEL_DYNAMIC: _DEFAULT_EXECUTION_MODEL_DYNAMIC,
    HINT_EXECUTION_MODEL_STATIC: _DEFAULT_EXECUTION_MODEL_STATIC,
    HINT_EXECUTION_MODEL_NATIVE: _DEFAULT_EXECUTION_MODEL_NATIVE,
}


def canonical_keys() -> tuple[str, ...]:
    """Return the set of recognised override keys."""
    return tuple(_DEFAULTS.keys())


def get_framework_hint(
    key: str,
    overrides: dict[str, str] | None = None,
) -> str | None:
    """Resolve a framework-hint block by canonical key.

    Looks up ``overrides`` first; if present (even if empty string) the
    override wins. Otherwise returns the built-in default.

    Args:
        key: A canonical key from :data:`_DEFAULTS` (see module docstring).
        overrides: Optional override map. Unknown keys in this dict are
            ignored with a WARNING log line — they won't crash aggregation.

    Returns:
        The prose for ``key``. An empty string indicates the block should
        be omitted entirely. Returns ``None`` if the key itself is not
        canonical (caller should treat this as a bug in the aggregator,
        not a user error).
    """
    if key not in _DEFAULTS:
        logger.warning("Unknown framework-hint key requested", hint_key=key)
        return None

    if overrides:
        # Warn about unknown keys in the override map exactly once per lookup.
        # (Aggregator calls get_framework_hint for each known key, so the
        # first call that sees a bad override will surface it.)
        _warn_unknown_overrides(overrides)
        if key in overrides:
            logger.debug("Framework-hint override applied", hint_key=key)
            return overrides[key]

    return _DEFAULTS[key]


def _warn_unknown_overrides(overrides: dict[str, str]) -> None:
    """Emit a single WARNING listing any unknown keys in ``overrides``."""
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
    """Merge package-level and creature-level override maps.

    Creature-level entries win over package-level entries for the same key.
    Returns an empty dict if both inputs are falsy. Unknown keys are
    preserved as-is; :func:`get_framework_hint` will warn about them on
    lookup.
    """
    merged: dict[str, str] = {}
    if package_level:
        merged.update(package_level)
    if creature_level:
        merged.update(creature_level)
    return merged
