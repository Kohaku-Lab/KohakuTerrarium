# Framework Internals

Notes for framework contributors on internal design decisions, structural constraints, and technical analysis. This document covers topics that are not part of the public API but matter when modifying the framework itself.

---

## In-Function Import Analysis

Current state of in-function imports in `src/kohakuterrarium/`.
Updated 2026-04-02 after the structural refactoring.

Previous audit (2026-04-01) found 32 runtime in-function imports. The
refactoring eliminated all but 2 of them. The old import cycle involving
`builtins.tools -> terrarium.runtime -> core.agent` is gone, replaced
by `tool_catalog` (a leaf module) with deferred loaders.


### Remaining In-Function Imports (2 total)

#### 1. `core/__init__.py:100` : core.agent.Agent, run_agent

  Pattern: Module-level `__getattr__` lazy loading.
  Import:  `from kohakuterrarium.core.agent import Agent, run_agent`

  Why: `core/__init__.py` eagerly imports `core.controller`, `core.config`,
  and other core submodules. `core.agent` transitively imports
  `builtins.inputs` (via bootstrap), which would cause a partial-
  initialization error if `Agent` were imported at module level in
  `__init__.py`.

  The lazy `__getattr__` approach has zero runtime cost for normal code
  paths, since all production code imports `Agent` directly from
  `core.agent`, not from `core/__init__`.

  Verdict: **LEGITIMATE** lazy load to avoid init-order issue.

#### 2. `terrarium/tool_registration.py:24` : builtins.tools.terrarium_tools

  Function: `ensure_terrarium_tools_registered()`
  Import:  `import kohakuterrarium.builtins.tools.terrarium_tools`

  Why: `terrarium_tools` uses `@register_builtin` decorators from
  `tool_catalog`. If `tool_registration.py` imported `terrarium_tools` at
  top level, the module would execute decorator calls during import,
  which must be deferred until the catalog is ready and the caller
  actually needs terrarium tools.

  This module registers itself as a deferred loader with `tool_catalog`
  via `register_deferred_loader()`. On first catalog miss, the loader
  triggers the import, which runs the decorators and populates the
  catalog. Subsequent lookups hit the cache directly.

  Verdict: **LEGITIMATE** deferred registration pattern.


### What Was Eliminated

The refactoring removed 30 in-function imports across these categories:

  a) stdlib/core-dependency imports that had no circular justification
     (asyncio, json, yaml, select, sys, openai, parsing.format,
     llm.tools, llm.message, core.environment)

  b) stale circular-avoidance imports where the original cycle had
     already been broken by earlier refactoring (core/agent_init.py
     imports of modules.subagent, builtins.subagents, parsing.format)

  c) the `builtins.tools.registry._ensure_terrarium_tools()` mechanism,
     replaced by `tool_catalog` deferred loaders and the dedicated
     `terrarium/tool_registration.py` module

  d) `terrarium/runtime.py` in-function import of `builtins.tools.registry`,
     replaced by direct import of `builtins.tool_catalog.get_builtin_tool`
     in the new `terrarium/factory.py`

  e) `core/config.py` yaml/json/tomllib imports moved to top level
     (tomllib/tomli try/except kept at module level)

  f) optional-dependency imports in `builtins/inputs/whisper.py` moved to
     module top level (the module is already guarded by try/except in
     `builtins/inputs/__init__.py`)


### Import Cycle Status

The largest strongly connected component (SCC) that previously involved
`builtins.tools`, `core.agent`, and `terrarium.runtime` is fully broken:

  Old path:
    `builtins.tools.registry -> builtins.tools.terrarium_tools
      -> terrarium.runtime -> core.agent -> core.agent_init
      -> builtins.tools.registry`

  Current state:
  - `tool_catalog` is a leaf module (no internal imports beyond `utils.logging`)
  - `terrarium/factory.py` imports `tool_catalog` directly (no cycle)
  - `terrarium/tool_registration.py` registers a deferred loader with
    `tool_catalog`, only importing `terrarium_tools` on demand

Two minor bidirectional dependencies remain (unchanged, both use
in-function imports placed inside convenience functions):

  Chain 1:
    `core/session.py -> core/scratchpad.Scratchpad`
    `core/scratchpad.py -> (in-function) core/session.get_session`

  Chain 2:
    `core/session.py -> core/channel.ChannelRegistry`
    `core/channel.py -> (in-function) core/session.get_session`

These are stable and low-cost. The convenience functions (`get_scratchpad`,
`get_channel_registry`) could be moved to `session.py` to eliminate the
cycles, but this is low priority.


### TYPE_CHECKING and Other Non-Runtime Imports

`TYPE_CHECKING` blocks and docstring examples are not counted above.
These are standard patterns and require no action. Notable
`TYPE_CHECKING` guards:

  - `core/agent.py`: Environment
  - `core/controller.py`: ToolSchema
  - `core/events.py`: ContentPart, TextPart
  - `builtins/tool_catalog.py`: BaseTool
  - `terrarium/api.py`: ChannelObserver, TerrariumRuntime
  - `terrarium/factory.py`: TerrariumRuntime
  - `terrarium/persistence.py`: TerrariumRuntime
  - `modules/tool/base.py`: ContentPart, ImagePart, TextPart
  - `prompt/plugins.py`: Registry
