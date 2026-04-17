# Terrariums

For readers composing several creatures that need to cooperate.

A **terrarium** is pure wiring: no LLM of its own, no decisions. It owns shared channels and manages the lifecycle of the creatures inside it. Creatures do not know they are in one — they listen on channel names, send on channel names, and the terrarium makes those names real.

Concept primer: [terrarium](../concepts/multi-agent/terrarium.md), [root agent](../concepts/multi-agent/root-agent.md), [channel](../concepts/modules/channel.md).

Terrariums are still experimental — see the [honest bit](#the-honest-bit) below before building anything production-facing.

## Config anatomy

```yaml
terrarium:
  name: swe-team
  root:
    base_config: "@kt-defaults/creatures/root"
  creatures:
    - name: swe
      base_config: "@kt-defaults/creatures/swe"
      channels:
        listen:   [tasks]
        can_send: [review, status]
    - name: reviewer
      base_config: "@kt-defaults/creatures/reviewer"
      channels:
        listen:   [review]
        can_send: [status]
  channels:
    tasks:   { type: queue }
    review:  { type: queue }
    status:  { type: broadcast }
```

- **`creatures`** — same inheritance and override rules as standalone creatures. Each creature additionally gets `channels.listen` / `channels.can_send`.
- **`channels`** — `queue` (one consumer per message) or `broadcast` (every subscriber gets every message).
- **`root`** — optional user-facing creature outside the terrarium; see below.

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
kt terrarium run @kt-defaults/terrariums/swe_team
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
kt terrarium info @kt-defaults/terrariums/swe_team
```

## Root agent pattern

A root is a standalone creature with terrarium-management tools attached. It sits **outside** the terrarium and drives it from above:

- Auto-listens to every creature channel.
- Receives `report_to_root`.
- Gets terrarium tools (`terrarium_create`, `terrarium_send`, `creature_start`, `creature_stop`, …).
- Is the user-facing interface when a terrarium runs in TUI/CLI mode.

Use a root when you want a single conversational surface; skip it for headless cooperative flows.

```yaml
terrarium:
  root:
    base_config: "@kt-defaults/creatures/root"
    system_prompt_file: prompts/team_lead.md
```

See [concepts/multi-agent/root-agent](../concepts/multi-agent/root-agent.md) for the design rationale.

## Hot-plug at runtime

From the root (via tools) or programmatically:

```python
await runtime.add_creature("tester", tester_agent,
                           listen=["review"], can_send=["status"])
await runtime.add_channel("hotfix", channel_type="queue")
await runtime.wire_channel("swe", "hotfix", direction="listen")
await runtime.remove_creature("tester")
```

Tool equivalents the root uses: `creature_start`, `creature_stop`, `terrarium_create`, `terrarium_send`.

Hot-plug is useful for provisioning ad-hoc specialists without restarting. Existing channels pick up the new listener; the new creature receives its channel topology in its system prompt.

## Observer for debugging

`ChannelObserver` is a non-destructive tap on any channel. Unlike a consumer, observers read without competing for queue messages. The dashboard uses this under the hood; programmatically:

```python
sub = runtime.observer.observe("tasks")
async for msg in sub:
    print(f"[tasks] {msg.sender}: {msg.content}")
```

`--observe` from `kt terrarium run` attaches observers to the listed channels and streams them in the TUI.

## Programmatic terrariums

```python
from kohakuterrarium.terrarium.runtime import TerrariumRuntime
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.core.channel import ChannelMessage

runtime = TerrariumRuntime(load_terrarium_config("@kt-defaults/terrariums/swe_team"))
await runtime.start()

tasks = runtime.environment.shared_channels.get("tasks")
await tasks.send(ChannelMessage(sender="user", content="Fix the auth bug."))

await runtime.run()
await runtime.stop()
```

For streaming, multi-tenant, or long-lived use, wrap with `KohakuManager`. See [Programmatic Usage](programmatic-usage.md).

## The honest bit

A terrarium's progress depends on each creature routing output to the right channel. If a model ignores the instruction, the team stalls. Prefer terrariums when:

- The workflow is explicit (fixed channel topology, predictable message shapes).
- The creatures are well-prompted and reliably follow their role instructions.
- You want hot-plug or observation.

Prefer sub-agents (vertical delegation inside one creature) when a parent can do the decomposition itself.

## Troubleshooting

- **Team stalls, no messages moving.** The sender creature probably forgot to call `send_message`. Use `--observe` to see channel traffic live; stronger prompts on the sender side usually resolve it.
- **Creature doesn't react to a channel message.** Confirm `listen` contains the channel name and the `ChannelTrigger` registered (`kt terrarium info` prints the wiring).
- **Root can't see what creatures are doing.** Root sees channels it listens to and `report_to_root`. Add `report_to_root` to relevant `can_send` lists.
- **Slow startup with many creatures.** Each creature starts its own LLM provider and trigger manager; expect roughly linear startup time.

Planned improvements (automatic round-output routing, root lifecycle observation, dynamic terrarium management) are tracked in [ROADMAP](../../ROADMAP.md).

## See also

- [Creatures](creatures.md) — each terrarium entry is a creature.
- [Composition](composition.md) — Python-side alternative when you need a small loop, not a full terrarium.
- [Programmatic Usage](programmatic-usage.md) — `TerrariumRuntime` + `KohakuManager`.
- [Concepts / terrarium](../concepts/multi-agent/terrarium.md) — why terrariums look the way they do.
