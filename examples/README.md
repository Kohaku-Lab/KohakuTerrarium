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

| Script | Pattern | Why code, not config? |
|--------|---------|----------------------|
| programmatic_chat | Agent as library (baseline) | Your code controls the conversation loop |
| run_terrarium | Terrarium from code | Programmatic terrarium lifecycle |
| discord_adventure_bot | Bot-owned interaction | discord.py owns the loop; agents are NPCs created/destroyed dynamically; game state machine in bot code; Discord UI (buttons/embeds) is the interface |
| debate_arena | Multi-agent turn-taking | Strict A→B→judge ordering that channels can't express; external convergence detection; your code is the referee |
| task_orchestrator | Dynamic agent topology | Number/type of specialists computed at runtime; DAG dependency execution; ephemeral agents created per sub-task |
