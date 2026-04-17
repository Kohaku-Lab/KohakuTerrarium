# Examples

For readers looking for runnable code and config to learn from.

The `examples/` tree groups runnable material by kind: standalone agent configs, terrarium configs, plugin implementations, and Python scripts that embed the framework. Each folder illustrates a pattern you can copy or inherit from.

Concept primer: [boundaries](../concepts/boundaries.md) — examples intentionally cover the edges.

## `examples/agent-apps/` — standalone creatures

Single-creature configs. Run with:

```bash
kt run examples/agent-apps/<name>
```

| Agent | Pattern | What it shows |
|---|---|---|
| `swe_agent` | Coding agent | Tool-heavy creature close to `kt-defaults/creatures/swe` |
| `discord_bot` | Group-chat bot | Custom Discord I/O, ephemeral, native tool calling |
| `planner_agent` | Plan-execute-reflect | Scratchpad state machine + critic sub-agent |
| `monitor_agent` | Trigger-driven | `input: none` + timer triggers, no user in the loop |
| `conversational` | Streaming ASR/TTS | Whisper input, TTS output, interactive sub-agent |
| `rp_agent` | Roleplay | Memory-first design, startup trigger, persona prompt |
| `compact_test` | Compaction stress | Small context + auto-compact, for validating the compaction path |

Related guides: [Creatures](creatures.md), [Configuration](configuration.md).

## `examples/terrariums/` — multi-agent configs

```bash
kt terrarium run examples/terrariums/<name>
```

| Terrarium | Topology | Creatures |
|---|---|---|
| `novel_terrarium` | Pipeline with feedback | brainstorm → planner → writer |
| `code_review_team` | Loop with gate | developer, reviewer, tester |
| `research_assistant` | Star with coordinator | coordinator + searcher + analyst |

Related guide: [Terrariums](terrariums.md).

## `examples/plugins/` — plugin hooks

One example per hook category. Use these as a reference when writing your own.

| Plugin | Hooks | Level |
|---|---|---|
| `hello_plugin` | `on_load`, `on_agent_start/stop` | beginner |
| `tool_timer` | `pre/post_tool_execute`, state | beginner |
| `tool_guard` | `pre_tool_execute`, `PluginBlockError` | intermediate |
| `prompt_injector` | `pre_llm_call` (message mutation) | intermediate |
| `response_logger` | `post_llm_call`, `on_event`, `on_interrupt` | intermediate |
| `budget_enforcer` | `pre/post_llm_call` with blocking + state | advanced |
| `subagent_tracker` | `pre/post_subagent_run`, `on_task_promoted` | advanced |
| `webhook_notifier` | Fire-and-forget callbacks, `inject_event`, `switch_model` | advanced |

Related guide: [Plugins](plugins.md). See `examples/plugins/README.md` for the full field-by-field walk-through.

## `examples/code/` — Python embedding

Scripts that embed the framework with your code as the orchestrator. Each uses a different slice of the compose algebra or the `Agent` / `TerrariumRuntime` / `KohakuManager` API.

| Script | Pattern | Features used |
|---|---|---|
| `programmatic_chat.py` | Agent as library | `AgentSession.chat()` |
| `run_terrarium.py` | Terrarium from code | `TerrariumRuntime`, channel injection |
| `discord_adventure_bot.py` | Bot-owned interaction | `agent()`, dynamic creation, game state |
| `debate_arena.py` | Multi-agent turn-taking | `agent()`, `>>`, `async for`, persistent agents |
| `task_orchestrator.py` | Dynamic agent topology | `factory()`, `>>`, `asyncio.gather` |
| `ensemble_voting.py` | Redundancy through diversity | `&`, `>>` auto-wrap, `\|`, `*` |
| `review_loop.py` | Write → review → revise | `.iterate()`, persistent `agent()` |
| `smart_router.py` | Classify and dispatch | `>> {dict}` routing, `factory()`, `\|` fallback |
| `pipeline_transforms.py` | Data-extraction pipeline | `>>` auto-wrap (`json.loads`, lambdas), agents + functions |

Related guides: [Programmatic Usage](programmatic-usage.md), [Composition](composition.md).

## Reading order for new readers

1. **Run something.** `kt run examples/agent-apps/swe_agent` — feel how a creature works.
2. **Inherit from it.** Copy the folder, tweak `config.yaml`, run again.
3. **Add a plugin.** Drop `examples/plugins/tool_timer.py` into your creature's `plugins:` list.
4. **Go Python.** Open `examples/code/programmatic_chat.py` and run it.
5. **Compose.** Try `examples/code/review_loop.py` for the compose algebra in action.
6. **Go multi-agent.** Run `examples/terrariums/code_review_team` and watch the channel traffic.

## See also

- [Getting Started](getting-started.md) — environment setup.
- [`kt-defaults`](../../kt-defaults/README.md) — the showcase package; examples share many of its patterns.
- [Tutorials](../tutorials/README.md) — guided walk-throughs that pair with these examples.
