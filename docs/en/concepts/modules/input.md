---
title: Input
summary: The specific-case trigger that carries user messages into the event queue.
tags:
  - concepts
  - module
  - input
---

# Input

## What it is

The **input** module is how the outside world hands work to the
creature. In the canonical derivation it sits before the controller and
fires the first event. In practice, it is just one specific kind of
trigger — the one labelled "user input" by convention.

## Why it exists

A creature that only responds to ambient triggers (timers, channels,
webhooks) cannot be chatted with. Most agents have a human at one end
of the loop, at least sometimes, and that human needs a place to type.

## How we define it

An `InputModule` implements one async method, `get_input()`, that
blocks until a `TriggerEvent` is ready. Whatever it returns gets pushed
onto the event queue exactly like a timer fire or a channel message.

This is why the docs keep saying "input is also a trigger" — it is,
structurally. The distinction is mostly about lifecycle (inputs are
usually foreground, triggers usually background) and intent (inputs
carry user content).

## How we implement it

Built-in input modules:

- **`cli`** — `prompt_toolkit`-powered line editor. Supports history,
  slash commands, multi-line, paste.
- **`cli_nonblocking`** — the same basic surface, but yields back to
  the event loop between keystrokes so triggers can still fire during
  input.
- **`tui`** — when the creature runs under Textual, the TUI composer
  is the input.
- **`none`** — a stub that never produces events; for purely
  trigger-driven creatures.

Audio/ASR implementations are intentionally not imported by the core package.
See `examples/agent-apps/conversational/custom/` for opt-in ASR and Whisper
input modules that can be loaded with `type: custom`.

Custom inputs register via `type: custom` or `type: package` in the
creature config. They must implement `InputModule` and are loaded by
`bootstrap/io.py`.

## What you can therefore do

- **Trigger-only creatures.** `input: { type: none }` plus one or more
  triggers: a cron creature, a channel watcher, a webhook receiver.
- **Multi-surface chat.** An HTTP-driven deployment does not need a
  CLI input — the `AgentSession` transport pushes user content in
  programmatically via `inject_input()`.
- **Sensor-style inputs.** Plug in a filesystem watcher, a Discord
  listener, or an MQTT consumer. The creature does not know the
  difference.
- **Input as policy.** An input module can transform what the user
  typed before it reaches the controller — translate language, run a
  moderation check, strip secrets.

## Don't be bounded

Inputs are optional. A Discord bot creature with no "human sitting at
a terminal" can omit input entirely and drive itself from an HTTP
WebSocket trigger. Conversely, a creature can have several effective
input surfaces — a user can type on the CLI while a webhook pushes
events and a timer fires alongside.

## See also

- [Trigger](trigger.md) — the general case; input is a specific shape of it.
- [reference/builtins.md — Inputs](../../reference/builtins.md) — the complete list of built-in input modules.
- [guides/custom-modules.md](../../guides/custom-modules.md) — how to write your own input.
