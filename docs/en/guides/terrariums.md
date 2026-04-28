---
title: Terrariums
summary: Horizontal multi-agent with channels, output wiring, root agents, hot-plug, and observation.
tags:
  - guides
  - terrarium
  - multi-agent
---

# Terrariums

For readers composing several creatures that need to cooperate.

A **terrarium** is the runtime engine that hosts every running creature in the process. A standalone agent is a 1-creature graph; a multi-agent team is a connected graph wired by channels. The engine owns lifecycles, shared channels, hot-plug, output wiring, and the session merge / split bookkeeping that follows topology changes. It has no LLM and makes no decisions — pure wiring. Creatures do not know they are in one; they listen on channel names, send on channel names, and the engine makes those names real.

A `terrarium.yaml` config is a **recipe**: a sequence of "add these creatures, declare these channels, wire these edges" applied to the engine. It is no longer a distinct kind of entity.

Concept primer: [terrarium](../concepts/multi-agent/terrarium.md), [root agent](../concepts/multi-agent/root-agent.md), [channel](../concepts/modules/channel.md).

We treat terrarium as a **proposed architecture** for horizontal multi-agent — the pieces work together (wiring + channels + hot-plug + observation + lifecycle pings to root), and kt-biome's four terrariums exercise them end to end. What we're still learning is the idiom; see [position, honestly](#position-honestly) below and the [ROADMAP](../../ROADMAP.md).

## Config anatomy

```yaml
terrarium:
  name: swe-team
  root:
    base_config: "@kt-biome/creatures/general"
    system_prompt_file: prompts/root.md    # team-specific delegation prompt, co-located with the terrarium
  creatures:
    - name: swe
      base_config: "@kt-biome/creatures/swe"
      output_wiring: [reviewer]            # deterministic edge: every swe turn → reviewer
      channels:
        listen:   [tasks, feedback]
        can_send: [status]
    - name: reviewer
      base_config: "@kt-biome/creatures/swe"
      system_prompt_file: prompts/reviewer.md   # reviewer role expressed as a prompt, not a dedicated creature
      channels:
        listen:   [status]
        can_send: [feedback, results, status]  # conditional: approve → results, revise → feedback
  channels:
    tasks:    { type: queue }
    feedback: { type: queue }
    results:  { type: queue }
    status:   { type: broadcast }
```

- **`creatures`** — same inheritance and override rules as standalone creatures. Each creature additionally gets `channels.listen` / `channels.can_send` plus optional `output_wiring`.
- **`channels`** — `queue` (one consumer per message) or `broadcast` (every subscriber gets every message).
- **`output_wiring`** — per-creature list of targets that receive this creature's turn-end output automatically. See [Output wiring](#output-wiring).
- **`root`** — optional user-facing creature outside the terrarium; see below. kt-biome does not ship a standalone `root` creature — each terrarium brings its own `prompts/root.md`.

Shorthand for channel description:

```yaml
channels:
  tasks: "work items the team pulls from"
```

Field reference: [reference/configuration](../reference/configuration.md).

## Auto-created channels

The runtime always creates:

- One `queue` per creature, named after it, so others can DM it.
- A `report_to_root` queue, if `root` is set.

You do not need to declare these.

## How channels connect

For each creature, for each `listen:` entry, the runtime registers a `ChannelTrigger` that fires the controller when a message arrives. The system prompt receives a short topology paragraph telling the creature which channels it listens to and which it can send to.

The `send_message` tool is auto-added; the creature sends by calling it with `channel` and `content` args. In the default bracket format that looks like:

```
[/send_message]
@@channel=review
@@content=...
[send_message/]
```

If your creature uses `tool_format: xml` or `native`, the call looks different; the semantics are the same. See [creatures — Tool format](creatures.md).

## Running a terrarium

```bash
kt terrarium run @kt-biome/terrariums/swe_team
```

Flags:

- `--mode tui|cli|plain` (default `tui`)
- `--seed "Fix the auth bug."` — inject a starter message on the seed channel
- `--seed-channel tasks` — override which channel receives the seed
- `--observe tasks review status` / `--no-observe` — channel observation
- `--llm <profile>` — override for every creature
- `--session <path>` / `--no-session` — persistence

In TUI mode you get a multi-tab view: root (if any), each creature, and observed channels. In CLI mode the first creature (or the root) mounts with RichCLI.

Terrarium info without running:

```bash
kt terrarium info @kt-biome/terrariums/swe_team
```

## Root agent pattern

A root is a standalone creature with terrarium-management tools attached. It sits **outside** the terrarium and drives it from above:

- Auto-listens to every creature channel.
- Receives `report_to_root`.
- Gets terrarium tools (`terrarium_create`, `terrarium_send`, `creature_start`, `creature_stop`, …).
- Auto-receives a generated "terrarium awareness" prompt section listing the bound team's creatures and channels.
- Is the user-facing interface when a terrarium runs in TUI/CLI mode.

Use a root when you want a single conversational surface; skip it for headless cooperative flows.

```yaml
terrarium:
  root:
    base_config: "@kt-biome/creatures/general"
    system_prompt_file: prompts/root.md   # team-specific delegation prompt
```

kt-biome does not ship a generic `root` creature. Each terrarium owns its own `root:` block and a co-located `prompts/root.md` — the prompt can name actual team members ("coding → send to `driver`") because it lives next to the team it orchestrates. The framework provides the management toolset and topology awareness automatically.

See [concepts/multi-agent/root-agent](../concepts/multi-agent/root-agent.md) for the design rationale.

## Hot-plug at runtime

From the root (via tools) or programmatically through the engine:

```python
from kohakuterrarium import Terrarium

async with Terrarium() as engine:
    await engine.apply_recipe("@kt-biome/terrariums/swe_team")
    tester = await engine.add_creature(
        "@kt-biome/creatures/swe", creature_id="tester",
    )
    # tester lands in its own singleton graph; connect() merges it in.
    swe = engine["swe"]
    result = await engine.connect(swe, tester, channel="review")
    # result.delta_kind == "merge"
```

Cross-graph `connect()` merges the two graphs — environments union, attached session stores merge into one, the new listener gets a `ChannelTrigger` injected. `disconnect()` may split a graph back apart and copy the parent session into each side. See [`examples/code/terrarium_hotplug.py`](../../examples/code/terrarium_hotplug.py).

Tool equivalents the root uses: `creature_start`, `creature_stop`, `terrarium_create`, `terrarium_send`.

Hot-plug is useful for provisioning ad-hoc specialists without restarting. Existing channels pick up the new listener; the new creature receives its channel topology in its system prompt.

## Observer for debugging

Channel observation is a non-destructive tap on channel traffic. Unlike a consumer, an observer reads without competing for queue messages. Programmatic code can subscribe to the engine event stream and filter for channel messages:

```python
from kohakuterrarium import EventFilter, EventKind

async for ev in engine.subscribe(EventFilter(kinds={EventKind.CHANNEL_MESSAGE})):
    print(f"[{ev.channel}] {ev.creature_id}: {ev.payload}")
```

The dashboard exposes this idea as an attach policy: it observes traffic without consuming the underlying channel messages.

## Programmatic terrariums

```python
import asyncio
from kohakuterrarium import Terrarium

async def main():
    engine = await Terrarium.from_recipe("@kt-biome/terrariums/swe_team")
    try:
        # talk to a creature by id
        async for chunk in engine["swe"].chat("Fix the auth bug."):
            print(chunk, end="", flush=True)
    finally:
        await engine.shutdown()

asyncio.run(main())
```

For more patterns (event subscription, hot-plug, solo + recipe coexistence) see [Programmatic Usage](programmatic-usage.md) and the runnable scripts in [`examples/code/`](../../examples/code/) (`terrarium_solo.py`, `terrarium_recipe.py`, `terrarium_hotplug.py`).

For management tasks above the runtime engine — packages, active-session handles, saved-session persistence, attach policies, and editor flows — use [`Studio`](studio.md).

## Output wiring

Channels rely on the creature remembering to call `send_message`. For pipeline edges that are deterministic — "every time the coder finishes, the runner should run what it wrote" — the framework offers an alternative: **output wiring**.

A creature declares in its config where its turn-end output should go. At every turn boundary, the framework emits a `creature_output` `TriggerEvent` into each target's event queue. No `send_message`, no `ChannelTrigger`, no channel in between.

```yaml
# terrarium.yaml creature block
- name: coder
  base_config: "@kt-biome/creatures/swe"
  output_wiring:
    - runner                              # shorthand = {to: runner, with_content: true}
    - { to: root, with_content: false }   # lifecycle ping (metadata only)
  channels:
    listen: [reverts, team_chat]
    can_send: [team_chat]
```

The full entry shape is in [reference / configuration — output wiring](../reference/configuration.md#output-wiring). Key properties:

- **`to: <creature-name>`** resolves to another creature in the same terrarium.
- **`to: root`** is a magic-string target — the root agent (which sits outside the terrarium). Useful for lifecycle pings; the root sees the event even when it wouldn't be listening to a channel.
- **`with_content: false`** delivers the event with empty `content` — a metadata-only "turn-end happened" signal.
- **`prompt` / `prompt_format`** customise the receiver-side prompt-override text.

### When to wire vs. when to channel

Reach for **output wiring** when:

- The edge is deterministic — one creature's output always goes to the next stage.
- You want lifecycle observability without the creature having to opt in via `send_message`.
- The pipeline is linear (or a ratchet loop where the loop-back is still unconditional).

Stay on **channels** when:

- The edge is conditional. Reviewer says approve vs. revise; analyzer says keep vs. discard. Wiring can't branch; channels can.
- The traffic is broadcast / status / team-chat — optional, observed by many.
- You want group-chat shape where many creatures may send, any may listen.

Both mechanisms compose freely in one terrarium. kt-biome's `auto_research` uses wiring for the ratchet edges (ideator → coder → runner → analyzer) and channels for the analyzer's keep-vs-discard decision and for team-chat status.

### How the receiver sees a wiring event

The event lands in the target creature's event queue and goes through the same `_process_event` path any trigger uses. The receiver tab in the TUI renders the ensuing turn normally (prompt injection, LLM text, tools). Plugins registered on the receiver see the event via the existing `on_event` hook — no new plugin API.

## Position, honestly

Two cooperation mechanisms are enough to cover most teams today: channels (tool + trigger, voluntary) and output wiring (framework-level, automatic). The kt-biome terrariums exercise both — wiring for the deterministic pipeline edges, channels for the conditional branches and group-chat traffic.

What we're still learning is the idiom. The observer panel and TUI rendering of wiring events is thinner than channel-traffic rendering. Conditional edges still need channels because wiring can't branch — a small `when:` filter is something we want to understand through use rather than design up front. Content modes (`last_round` vs. `all_rounds` vs. summary) may become useful for pipelines that want scratch reasoning included; not clear yet. See the [ROADMAP](../../ROADMAP.md) for the full set of open questions.

Prefer **sub-agents** (vertical delegation inside one creature) when a single parent can do the decomposition itself — it's the simpler answer for most "I need context isolation" instincts. Reach for a terrarium when you genuinely want different creatures cooperating and want the individual creatures to stay portable as standalone configs.

## Troubleshooting

- **Team stalls, no messages moving.** Most common cause: the sender relied on `send_message` and the LLM forgot. Two fixes:
  - Add `output_wiring:` for the deterministic pipeline edge — the framework can't forget.
  - Strengthen the sender's prompt about the channel obligation (for conditional edges that must remain channel-based).
  Use `--observe` to see channel traffic live.
- **Creature doesn't react to a channel message.** Confirm `listen` contains the channel name and the `ChannelTrigger` registered (`kt terrarium info` prints the wiring).
- **Root can't see what creatures are doing.** Two paths: add `report_to_root` to the creature's `can_send` (channel-based), or add `{to: root, with_content: false}` to its `output_wiring` (framework-level lifecycle ping; fires even if the creature never calls `send_message`).
- **Wiring target doesn't receive anything.** Check that the target creature exists in the same terrarium and is running. Wiring resolves by creature name (or the magic `root` token); unknown / stopped targets are logged and skipped.
- **Slow startup with many creatures.** Each creature starts its own LLM provider and trigger manager; expect roughly linear startup time.

## See also

- [Creatures](creatures.md) — each terrarium entry is a creature.
- [Composition](composition.md) — Python-side alternative when you need a small loop, not a full terrarium.
- [Programmatic Usage](programmatic-usage.md) — the `Terrarium` engine.
- [Concepts / terrarium](../concepts/multi-agent/terrarium.md) — why terrariums look the way they do.
