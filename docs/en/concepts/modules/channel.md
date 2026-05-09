---
title: Channel
summary: Named broadcast pipes that underpin multi-agent and cross-module communication.
tags:
  - concepts
  - module
  - channel
  - multi-agent
---

# Channel

## What it is

A **channel** is a named message pipe. One end can send; every
listener receives every send. Channels live either in a creature's
private session or in a [graph](../glossary.md#graph)'s shared
environment that multiple creatures see.

They are not strictly a "canonical" module of the creature — they
never appeared in the chat-bot → agent derivation. They are the
communication substrate that makes tools and triggers actually useful
across agents.

## Why it exists

Once you have tools and triggers, you want two agents to talk to each
other. The lowest-friction way is to say: agent A's tool writes a
message; agent B has a trigger that fires when a message arrives on
that name.

That is exactly what a channel is. It is not a new idea — it is a
*naming convention* plus a small amount of queueing machinery, so that
"write here, listen there" works without either side knowing about
the other.

## How we define it

A graph channel is broadcast: every listener subscribed to it
receives every message that any sender writes. There is no
queue-vs-broadcast choice at the graph layer — all
[terrarium](../multi-agent/terrarium.md) channels are broadcast.

Channels live in a `ChannelRegistry`. A creature's private session
has one registry; a graph's shared environment has another. A
creature can listen on channels in either.

`ChannelTrigger` binds a channel name to a creature's event stream —
whenever a message arrives, a `channel_message` event is pushed.

## How we implement it

`core/channel.py` defines the channel primitive and the registry.
The terrarium engine always registers graph channels as broadcast
(`terrarium/channels.py`), so a creature listening on a channel sees
every send to that channel, in the order they arrived.
`modules/trigger/channel.py` implements the trigger that bridges a
channel into a creature's event queue. There is also an internal
queue primitive (`SubAgentChannel`) used inside a single creature
for sub-agent stdout / parent-controller plumbing — that's a
private implementation detail, not a graph channel.

Auto-created channels (the engine adds these without you declaring
them):

- One channel per creature in a graph, named after the creature
  (so others can DM it via `send_channel`).
- `report_to_root` when a recipe declares `root:`, with every other
  creature wired to send on it and only root listening.

You can subscribe to the engine event stream non-destructively to
observe channel messages without competing with consumers — every
send emits a `CHANNEL_MESSAGE` `EngineEvent`. That's how dashboards
watch traffic without participating in it.

## What you can therefore do

- **Terrarium wiring.** Every listen/send line in a terrarium config
  resolves to channel operations.
- **Group-chat pattern.** `send_message` tool (by any creature) +
  `ChannelTrigger` (on other creatures) = N-way group chat. No new
  primitive needed.
- **Dead-letter / failure channels.** Route errors to a dedicated
  channel; a `logger` creature subscribes and writes them to disk.
- **Non-destructive debugging.** Subscribe to the engine event
  stream to snoop on channel traffic without participating as a
  listener.
- **Cross-creature rendezvous.** Two creatures that each listen on the
  same shared channel can take turns handling items.

## Channels vs. output wiring

Channels aren't the only way creatures talk. A sibling mechanism —
**output wiring** — emits a `creature_output` `TriggerEvent` straight
into a target creature's event queue at the end of every turn, with
no `send_message` call on either side. Which one to use:

- **Channels** — conditional routes (approve vs. revise), group chat,
  status, late / optional traffic, observation. The creature chooses
  whether and where to send.
- **Output wiring** — deterministic pipeline edges ("the runner's
  output always goes to the analyzer"). Configured declaratively;
  fires automatically at turn-end.

A single terrarium freely mixes both. See
[terrarium](../multi-agent/terrarium.md) and
[guides/terrariums](../../guides/terrariums.md#output-wiring).

## Don't be bounded

A standalone creature does not need channels — its tools do not
`send_message`, its triggers do not listen. A channel is not a
first-class module in the derivation; it is a convention that the
framework happens to provide as a primitive because so many multi-
agent use cases reduce to it.

This is the clearest example of "the framework bends its own
abstractions." A channel lives outside the six-module taxonomy, and
implementing "agent A tells agent B something" as "tool writes, trigger
fires" mixes layers on purpose. See [boundaries](../boundaries.md).

## See also

- [Tool](tool.md) — the sending half.
- [Trigger](trigger.md) — the receiving half.
- [Multi-agent / terrarium](../multi-agent/terrarium.md) — where channels light up as wiring.
- [Patterns](../patterns.md) — group chat, dead-letter, observer.
