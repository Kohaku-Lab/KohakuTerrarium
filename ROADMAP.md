# Roadmap

KohakuTerrarium 1.0.0 focuses on delivering a solid core runtime. This roadmap highlights areas we want to improve next, based on both current limitations and likely user needs.

## Terrarium improvements

Horizontal multi-agent originally leaned on each creature reliably calling `send_message` to pass its output to the next stage. That worked when the creature followed the instruction, and stalled when it didn't. The framework now proposes a concrete answer; the sections below separate what shipped from what we're still exploring.

### Shipped (first cut)

- **Configurable automatic round output routing — `output_wiring`.** Per-creature config field that auto-delivers a creature's turn-end text into one or more target agents' event queues as a framework-emitted `creature_output` TriggerEvent. No channel round-trip, no dependency on the LLM remembering `send_message`. See [`plans/output-wiring.md`](plans/output-wiring.md) and the `output_wiring:` entry in [the configuration reference](docs/reference/configuration.md).
- **Root creature lifecycle observation.** Same feature with `{to: root, with_content: false}` per wiring entry — every turn-end pings root with the lifecycle signal; content toggle decides whether the payload is carried. One mechanism, two use cases.
- **Configuration-first.** Defaults to off; opt in per-creature. No silent framework behaviour.

Channels remain the right answer for conditional / optional / group-chat traffic (analyzer → keep vs. discard, reviewer → approve vs. revise, team-wide status). Output wiring is the right answer for deterministic pipeline edges.

### Exploring further

The first cut covers the common pipeline shape. These are the rougher edges we still want to understand:

- **UI surfacing of wiring events.** Receiver tabs already render wiring-delivered turns as normal processing, but source-side and observer-tab visibility is thinner than channel-traffic rendering. A unified round-start activity firing at real round-begin (not at trigger-arrival) would also fix channel triggers leaking into mid-stream output today.
- **Conditional wiring.** Analyzer and reviewer shapes still need channels because `output_wiring` can't branch on an LLM decision. A small `when:` predicate (matching on turn output, status, or last-sent channel) would absorb more cases — but we want to see real usage before designing the filter DSL.
- **Content modes.** Current `content` is the last round's assistant text. A future `content_mode: last_round | all_rounds | summary` could help pipelines that want scratch reasoning included. Not yet clear it's worth the surface area.
- **Dynamic terrarium management.** Hot-plug add/remove of creatures and channels already exists; hot-plug add/remove of *wiring edges* is the natural extension.
- **Wiring-aware observer.** `ChannelObserver` taps channel activity; an equivalent view of the `creature_output` event stream would let dashboards show the full wiring graph in motion.

We're treating these as questions to answer through use, not fixed milestones.

## UI system improvements

The current UI layer is intentionally separate from the core framework. That is good for flexibility, but it also makes it harder for tools and plugins to produce richer UI experiences without custom frontend work.

### Special output modules

We want to support structured output modules that can render richer content than plain text or the default tool result accordion.

Goals:

- provide a unified output model for CLI, TUI, and frontend surfaces
- let tools and plugins emit structured UI events instead of only text
- make custom integrations easier without requiring full UI rewrites

### Special interaction modules

We also want a standard way for tools and plugins to request user interaction, such as:

- approval
- feedback
- answering questions
- selection from options

A likely design is event-based: a tool emits an interaction request, the UI handles it, and the result is returned back to the tool through a defined callback, polling, or listener mechanism.

### Summary

The general direction is a more modular UI system, so custom modules can participate in richer interfaces in a consistent and configurable way.

## More built-in creatures, terrariums, and plugins

`kt-biome` currently provides a useful starting set of out-of-the-box creatures, terrariums, and example plugins. After 1.0.0, we want to expand that set based on real user feedback and practical usage patterns.

Possible areas include:

- **RAG / memory plugins and tools**
  - including more seamless agent-oriented memory workflows
- **Improved terrarium setups**
  - especially the terrarium reliability ideas described above
- **Dynamic terrarium management**
  - allowing a root creature to create or remove creatures during runtime
- **Permission guard / approval plugins**
  - for safer execution and user confirmation workflows
- **Computer use tools**
- **Messaging platform integrations**
  - such as Discord, Telegram, and similar systems

## Daemon and UI decoupling

Today, CLI and TUI usage often requires keeping the terminal session open. That is not always ideal for SSH sessions, remote servers, or long-running tasks.

A future direction is to use `kt serve` as the backend for more UI modes, so agents can continue running independently of the local terminal session. That would make it easier to:

- disconnect without stopping work
- reconnect from another interface later
- run long-lived agents more comfortably on remote machines
