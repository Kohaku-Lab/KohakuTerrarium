# Roadmap

KohakuTerrarium 1.0.0 focuses on delivering a solid core runtime. This roadmap highlights areas we want to improve next, based on both current limitations and likely user needs.

## Terrarium improvements

One current weakness in terrarium workflows is that progress can depend on each creature explicitly sending output to the right place. If a model ignores that instruction, the terrarium can become stale or fail to move forward as intended.

Planned directions include:

- **Configurable automatic round output routing**
  - Let users assign a channel that receives a creature's latest message for a round automatically.
  - This would make terrarium flow more reliable and closer to how sub-agent final outputs are handled.
- **Root creature lifecycle observation**
  - Let the root creature receive completion signals from other creatures.
  - This would allow it to inspect channel activity, detect idle states, and decide whether another round should begin.
- **Configuration-first behavior**
  - Any automation in this area should remain configurable rather than hard-coded.

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

`kt-defaults` currently provides a useful starting set of out-of-the-box creatures, terrariums, and example plugins. After 1.0.0, we want to expand that set based on real user feedback and practical usage patterns.

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
