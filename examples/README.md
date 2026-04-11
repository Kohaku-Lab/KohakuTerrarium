# Examples

## Agent Apps (`agent-apps/`)

Single-agent configurations demonstrating different architecture patterns.
Each is a complete creature config runnable with `kt run`.

```bash
kt run examples/agent-apps/<name>
```

| Agent | Pattern | Key Feature |
|-------|---------|-------------|
| discord_bot | Group chat bot | Custom Discord I/O, ephemeral, native tool calling |
| planner_agent | Plan-execute-reflect | Scratchpad tracking, critic review |
| monitor_agent | Trigger-driven monitoring | No user input, timer triggers |
| conversational | Streaming ASR/TTS | Whisper input, interactive output sub-agent |
| rp_agent | Character roleplay | Memory-first, startup trigger |
| compact_test | Compaction stress test | Auto-compact with small context |

## Terrariums (`terrariums/`)

Multi-agent configurations demonstrating creature coordination.

```bash
kt terrarium run examples/terrariums/<name>
```

| Terrarium | Topology | Creatures |
|-----------|----------|-----------|
| novel_terrarium | Pipeline with feedback loop | brainstorm → planner → writer |
| code_review_team | Loop with gate (review → approve/reject) | developer, reviewer, tester |
| research_assistant | Star with coordinator | coordinator, searcher, analyst |

## Plugins (`plugins/`)

Educational plugin examples demonstrating every hook type in the plugin API.
See [`plugins/README.md`](plugins/README.md) for the full reference.

| Plugin | Hooks | Difficulty |
|--------|-------|------------|
| hello_plugin | Lifecycle: `on_load`, `on_agent_start/stop` | Beginner |
| tool_timer | `pre/post_tool_execute`, state persistence | Beginner |
| tool_guard | `pre_tool_execute`, `PluginBlockError` (blocking) | Intermediate |
| prompt_injector | `pre_llm_call` (message modification) | Intermediate |
| response_logger | `post_llm_call`, `on_event`, `on_interrupt`, `on_compact_end` | Intermediate |
| budget_enforcer | `post_llm_call` + `pre_llm_call` (blocking), state | Advanced |
| subagent_tracker | `pre/post_subagent_run`, `on_task_promoted` | Advanced |
| webhook_notifier | All callbacks, `inject_event`, `switch_model` | Advanced |

## Code (`code/`)

Programmatic usage — embedding agents in your own applications.

The key distinction from config-based usage: **your program is the
orchestrator, agents are workers you invoke.** The agent doesn't run
itself — you control when, what, and how it processes.

Uses the **composition algebra** (`kohakuterrarium.compose`):

```python
from kohakuterrarium.compose import agent, factory

# Persistent agent (reused across calls, accumulates context)
async with await agent("@kt-defaults/creatures/swe") as swe:
    result = await (swe >> extract_code >> reviewer)(task)

# Ephemeral agent (fresh per call, no state carry-over)
specialist = factory(make_config("coder"))
result = await (specialist >> validate)(task)

# Operators: >> (sequence), & (parallel), | (fallback), * (retry)
safe = (expert * 2) | generalist
results = await (analyst & writer & designer)(task)

# Loop with native control flow
async for result in (writer >> reviewer).iterate(task):
    if "APPROVED" in result:
        break
```

| Script | Pattern | Compose features used |
|--------|---------|----------------------|
| programmatic_chat | Agent as library (baseline) | `AgentSession.chat()` |
| run_terrarium | Terrarium from code | `TerrariumRuntime` API |
| discord_adventure_bot | Bot-owned interaction | `agent()`, dynamic creation, game state |
| debate_arena | Multi-agent turn-taking | `agent()`, `>>`, `async for`, `async with` |
| task_orchestrator | Dynamic agent topology | `factory()`, `>>`, `asyncio.gather` |
| ensemble_voting | Redundancy through diversity | `&` (parallel), `>>` auto-wrap, `\|` fallback, `*` retry |
| review_loop | Write → review → revise cycle | `async for` iterate, `>>` transforms, persistent `agent()` |
| smart_router | Classify → route to specialist | `>> dict` routing, `factory()`, `\|` fallback |
| pipeline_transforms | Data extraction pipeline | `>>` auto-wrap (json.loads, lambdas), mix agents + functions |
