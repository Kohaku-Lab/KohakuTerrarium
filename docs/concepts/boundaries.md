# Boundaries

The creature abstraction is the default shape of an agent in
KohakuTerrarium. It is *not* a law. This page collects the cases where
ignoring the default is the right move — and the cases where the
framework is not a good fit at all.

## The abstraction is a default, not a corset

The six modules (Controller, Input, Trigger, Tool, Sub-agent, Output)
appear together in most creatures, but each is independently optional:

- **No input.** `input: { type: none }`. A cron creature, a
  webhook-only receiver, a background monitor — none need a user
  typing.
- **No triggers.** A pure request/response chat creature works fine
  without any ambient wake-ups.
- **No tools.** A response-only specialist (summariser, formatter,
  translator) can run without any. The LLM is still powerful on its
  own.
- **No sub-agents.** Short-task creatures that never delegate are
  normal.
- **No output.** Side-effect-only creatures exist. A creature whose
  only job is to write to an external database does not need a sink.
- **No memory / compaction / session.** `--no-session` and
  `compact.enabled: false` cover the case.

The framework does not privilege the full six-module shape. It just
makes it cheap when you want it.

## The framework bends its own abstractions

This is not a leak, it is the point.

**Case: channels.** A channel did not appear in the chat-bot → agent
derivation. It is a communication substrate introduced for multi-
agent, and the simplest implementation is "a tool writes a message;
a trigger fires when one arrives." That mixes two modules for the
sake of one concept. It is also the natural way to do it, and
pretending otherwise would introduce a new primitive that buys
nothing.

**Case: root agent.** A root is "a creature with a specific toolset
and a specific listening wiring." Structurally it is not different
from any other creature; conceptually its position matters. We call
it out as a distinct role because the distinction is useful, not
because the framework enforces it.

The framework's abstractions are tools for thinking, not walls.

## When KohakuTerrarium fits

- Your requirements for an agent system are **unstable or evolving**.
  You don't yet know what tools, triggers, or prompts will survive
  the next round. A framework pays off when the thing you are
  building will change shape.
- You want to **try a new agent design** — a novel combination of
  tools, triggers, or sub-agent shapes — without rebuilding the
  substrate.
- You want **OOTB creatures you can customise**. `kt-defaults` gives
  a starting point; inherit from it, swap a few modules, done.
- You want to **embed agent behaviour inside existing Python code**
  without running a separate service.
- You want a **framework in which to share reusable pieces** (a
  package containing creatures, plugins, tools, and presets) between
  teams or across projects.

## When it does not fit

- You are **happy with an existing agent product** and your
  requirements are **stable**. If Claude Code, OpenClaw, or a
  pre-built internal tool already does what you need and you do not
  expect your needs to shift, switching costs you without paying you
  back.
- Your **mental model does not match the framework's.** If you think
  about agents in a way that does not map onto
  controller/tools/triggers/sub-agents/channels, forcing the fit
  makes things worse, not better. Use something else — or write a
  different framework.
- Your workload needs **ultra-low latency** — sub-50 ms per-operation
  matters to you. KohakuTerrarium is optimised for correctness and
  flexibility; the asyncio overhead, the event queue, the output
  router, the session persistence all cost a little. Usually fine;
  sometimes not.
- You just **don't want to use it.** That is a valid reason. A
  framework has no business being in a codebase whose maintainers
  resent it.

## Treat this page as permission

The opening question of concept docs is "what is this?" The closing
question is "is this for me?" If any of the *does-not-fit* cases
describe you, the right move is to use something else (or nothing)
and it is not a failure of the framework. If some mix of the *fits*
cases describes you, the rest of the docs are for you.

## See also

- [Why KohakuTerrarium](foundations/why-kohakuterrarium.md) — the framing that motivates
  the framework.
- [What is an agent](foundations/what-is-an-agent.md) — the canonical
  derivation this page lets you deviate from.
- [Patterns](patterns.md) — ways to combine modules that break the
  "one module = one purpose" intuition on purpose.
- [ROADMAP](../../ROADMAP.md) — where the rough parts are going.
