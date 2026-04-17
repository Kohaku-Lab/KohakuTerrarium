# Channel

## What it is

A **channel** is a typed message pipe. One end can send; the other
end(s) can receive. Channels live either in a creature's private
session or in a shared environment that multiple creatures see.

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

Two channel types:

- **`SubAgentChannel` (queue)** — messages FIFO, *one* receiver
  consumes each message. Good for request/response or task dispatch.
- **`AgentChannel` (broadcast)** — each subscriber has its own queue,
  every message reaches every subscriber. Good for announcements.

Channels live in a `ChannelRegistry`. A creature's private session has
one registry; a terrarium's environment has a shared registry. A
creature can listen on either.

`ChannelTrigger` binds a channel name to a creature's event stream —
whenever a message arrives, a `channel_message` event is pushed.

## How we implement it

`core/channel.py` implements the two channel classes and the registry.
`modules/trigger/channel.py` implements the trigger that bridges a
channel into a creature's event queue.

Auto-created channels:

- One **queue** per creature in a terrarium, named after the creature
  (so others can DM it).
- `report_to_root` when a root agent is present.

`ChannelObserver` (`terrarium/observer.py`) attaches a non-destructive
callback to a channel: the observer sees every message that is sent,
without consuming them. That is how dashboards watch queue channels
that have real consumers.

## What you can therefore do

- **Terrarium wiring.** Every listen/send line in a terrarium config
  resolves to channel operations.
- **Group-chat pattern.** `send_message` tool (by any creature) +
  `ChannelTrigger` (on other creatures) = N-way group chat. No new
  primitive needed.
- **Dead-letter / failure channels.** Route errors to a dedicated
  broadcast channel; a `logger` creature subscribes and writes them
  to disk.
- **Non-destructive debugging.** Use `ChannelObserver` to snoop on a
  queue that real consumers are already draining.
- **Cross-creature rendezvous.** Two creatures that each listen on the
  same shared channel can take turns handling items.

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
