# First Custom Tool

**Problem:** your agent needs a capability that no built-in tool
provides. You want to give it a new function the LLM can call.

**End state:** a `BaseTool` subclass living inside your creature
folder, wired in `config.yaml`, loaded at runtime, and invoked by the
LLM on request.

**Prerequisites:** [First Creature](first-creature.md). You should have
a creature folder you own.

The tool example here is a trivial `wordcount` — counts words in a
string. The point is the shape, not the logic. See
[tool concept](../concepts/modules/tool.md) for what tools *can* be
beyond simple functions.

## Step 1 — Pick a folder

Create a creature folder that will own this tool. We will call it
`creatures/tutorial-creature/`. The tool source lives alongside the
config:

```text
creatures/tutorial-creature/
  config.yaml
  prompts/
    system.md
  tools/
    wordcount.py
```

Make the directories:

```bash
mkdir -p creatures/tutorial-creature/prompts
mkdir -p creatures/tutorial-creature/tools
```

## Step 2 — Write the tool

`creatures/tutorial-creature/tools/wordcount.py`:

```python
"""Word count tool — counts words in a given text."""

from typing import Any

from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
)


class WordCountTool(BaseTool):
    """Count the words in a string."""

    @property
    def tool_name(self) -> str:
        return "wordcount"

    @property
    def description(self) -> str:
        # One line — goes straight into the system prompt.
        return "Count the words in a given piece of text."

    @property
    def execution_mode(self) -> ExecutionMode:
        # Pure, fast, in-memory — direct mode. See Step 5.
        return ExecutionMode.DIRECT

    # The JSON schema the LLM sees for args.
    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to count words in.",
            }
        },
        "required": ["text"],
    }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        text = args.get("text", "")
        count = len(text.split())
        return ToolResult(
            output=f"{count} words",
            metadata={"count": count},
        )
```

Key points:

- Subclass `BaseTool`. Implement `tool_name`, `description`, and
  `_execute`. The public `execute` wrapper on `BaseTool` already does
  the try/except and returns a `ToolResult(error=...)` on exceptions.
- `parameters` is a JSON-Schema-compatible dict. The controller uses
  it to build the tool schema the LLM sees.
- `_execute` is async. Return a `ToolResult`. `output` is either a
  string or a list of `ContentPart` for multimodal results.
- If your tool needs working directory / session / scratchpad, set
  `needs_context = True` on the class and accept `context` as a
  keyword argument in `_execute`. See
  [tool concept](../concepts/modules/tool.md) for the full
  `ToolContext` surface.

## Step 3 — Wire it into the creature config

`creatures/tutorial-creature/config.yaml`:

```yaml
name: tutorial_creature
version: "1.0"
base_config: "@kt-defaults/creatures/general"

system_prompt_file: prompts/system.md

tools:
  - name: wordcount
    type: custom
    module: ./tools/wordcount.py
    class_name: WordCountTool
```

What each field does:

- `type: custom` — load from a local Python file (as opposed to
  `builtin` or `package`).
- `module` — path to the `.py` file, resolved relative to the agent
  folder (`creatures/tutorial-creature/`).
- `class_name` — the class inside that module.

Because `tools:` extends the inherited list, you keep the full `general`
tool set and add `wordcount` on top.

`creatures/tutorial-creature/prompts/system.md`:

```markdown
# Tutorial Creature

You are a helpful assistant for text experiments. When a user asks
about word counts, prefer the `wordcount` tool.
```

## Step 4 — Run and try it

```bash
kt run creatures/tutorial-creature --mode cli
```

Prompt it:

```text
> Count the words in "hello world foo bar"
```

The controller should call `wordcount` with `text="hello world foo bar"`
and surface the result (`4 words`). On exit, `kt` prints the usual
resume hint. If you need to see it trigger reliably, use a fresh
session (pass `--no-session` for a throwaway run).

## Step 5 — Pick the right execution mode

Tools come in three execution modes:

| Mode | When to use | Example built-in |
|---|---|---|
| `DIRECT` | Fast, pure, completes within the turn. Result is awaited before the next LLM call. | `wordcount`, `read`, `grep` |
| `BACKGROUND` | Long-running (seconds+). Returns a job handle; result arrives as a later event. The LLM can keep working. | `bash` (long commands), sub-agents |
| `STATEFUL` | Multi-turn interaction. The tool yields; the agent reacts; the tool yields again. | Stateful wizards, REPLs |

`BaseTool` defaults to `BACKGROUND`. Override `execution_mode` (as the
sample does) when that is wrong. Pure-compute, sub-100-ms tools should
be `DIRECT`.

The execution pipeline is in
[tool concept — How we implement it](../concepts/modules/tool.md#how-we-implement-it).
Streams start tools as soon as the closing block is parsed; multiple
`DIRECT` tools run in parallel via `asyncio.gather`.

## Step 6 — Test it with ScriptedLLM (optional)

For unit tests, drive the controller with a deterministic LLM. The
`kohakuterrarium.testing` package ships helpers:

```python
import asyncio

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry


async def test_wordcount() -> None:
    agent = Agent.from_path("creatures/tutorial-creature")
    agent.llm = ScriptedLLM([
        ScriptEntry('[/wordcount]{"text": "one two three"}[wordcount/]'),
        ScriptEntry("Done — 3 words."),
    ])

    await agent.start()
    try:
        await agent.inject_input("count words in 'one two three'")
    finally:
        await agent.stop()


asyncio.run(test_wordcount())
```

The tool-call syntax in the script depends on the creature's
`tool_format` (`bracket` / `xml` / `native`). For native function
calling, use the provider-shaped call; for `bracket` (default in the
SWE creature's ancestor), use `[/name]{json}[name/]`.

See `src/kohakuterrarium/testing/` for `OutputRecorder`,
`EventRecorder`, and `TestAgentBuilder`.

## What you learned

- A tool is a `BaseTool` subclass with `tool_name`, `description`,
  `parameters`, and `_execute`.
- `tools:` in `config.yaml` wires it with `type: custom`, `module:`,
  and `class_name:`.
- Execution mode matters — pick `DIRECT` for fast pure work,
  `BACKGROUND` for long work.
- Tests can drive the whole flow deterministically with
  `ScriptedLLM`.

## What to read next

- [Tool concept](../concepts/modules/tool.md) — what a tool *can* be
  (message bus, state handle, agent wrapper, etc.).
- [Custom modules guide](../guides/custom-modules.md) — tools,
  sub-agents, triggers, and outputs together.
- [First plugin](first-plugin.md) — when the behaviour you want lives
  at the seams between modules, not inside one.
