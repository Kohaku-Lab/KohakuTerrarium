---
title: Module layout in 2.2
summary: Where modules moved in 2.2 and how to update imports in plugins, tools and packages.
tags:
  - reference
  - migration
---

# Module layout in 2.2

Release 2.2 reorganised `src/kohakuterrarium/` so no directory holds a long
run of files that share a prefix. There are no compatibility shims: update
imports when you move a package to 2.2. A package that requires 2.2 says so
with `framework: ">=2.2.0,<3.0.0"` in `kohaku.yaml` and
`kohakuterrarium>=2.2.0` in its dependencies.

## The rule

A family of files that shared a prefix became a package with the prefix
dropped, so `core/agent_compact.py` is now `core/agent/history/compact.py`.
A class too large for one file is split into mixins per concern and
assembled in one module (`core/agent/assembly.py`, `session/store/assembly.py`);
the class you import keeps its name.

## Paths that third-party code commonly imported

| Before | After |
|---|---|
| `core.config_types` | `core.config.types` |
| `core.config.load_agent_config` | `core.config.load.load_agent_config` |
| `core.agent_compact`, `core.agent_model` | `core.agent.history.compact`, `core.agent.model` |
| `llm.anthropic_provider` | `llm.anthropic.provider` |
| `llm.anthropic_format` | `llm.anthropic.format` |
| `llm.openai.OpenAIProvider` | `llm.openai.provider.OpenAIProvider` |
| `llm.openai_sanitize` | `llm.openai.sanitize` |
| `llm.codex_auth`, `llm.codex_provider` | `llm.codex.auth`, `llm.codex.provider` |
| `llm.api_keys`, `llm.backends` | `llm.profiles.api_keys`, `llm.profiles.backends` |
| `llm.profiles.resolve_controller_llm` | `llm.profiles.resolve.resolve_controller_llm` |
| `llm.recovery.{ErrorClass,RetryPolicy,classify_openai_error,backoff_delay}` | `llm.recovery.errors.*` |
| `llm.stream_replay`, `llm.turn_segments` | `llm.stream.replay`, `llm.stream.segments` |
| `terrarium.autosession` | `terrarium.session.autosession` |
| `terrarium.channels.TERRARIUM_ENGINE_KEY` | `terrarium.channels.channels.TERRARIUM_ENGINE_KEY` |
| `session.embedding` | `session.memory.embedding` |
| `session.memory.SessionMemory` | `session.memory.index.SessionMemory` |
| `modules.plugin.manager.PluginManager` | `modules.plugin.manager.manager.PluginManager` |
| `builtins.tools.grep`, `builtins.tools.read` | `builtins.tools.files.grep`, `builtins.tools.files.read` |
| `builtins.tools.bash.ShellTool` | `builtins.tools.bash.bash.ShellTool` |
| `laboratory.ClientConnector` | `laboratory._internal.client.ClientConnector` |

All paths are relative to `kohakuterrarium`. To look up a class you imported
before, search for its name under `src/kohakuterrarium/`; the package
`README.md` files describe each family.

## Tool lookup without a module path

To get a built-in tool without depending on where its file lives, ask the
catalog: `kohakuterrarium.builtins.tool_catalog.get_builtin_tool("bash")`.

## Test helpers

Tests that need a deterministic model use `kohakuterrarium.testing.llm`
(`ScriptedLLM`) and `kohakuterrarium.testing.llm_seam` (`install_llm_seam`,
`forbid_real_providers`, `LLM_FACTORY_TARGETS`) instead of patching each
provider factory by hand.

## Package tests that patched the packages directory

The packages directory follows `KT_CONFIG_DIR`: set that variable to a
temporary directory and the packages live in `<dir>/packages`. Code that
assigned the `PACKAGES_DIR` attribute of the `packages` module should call
`kohakuterrarium.packages.locations.packages_dir()` to read it.
