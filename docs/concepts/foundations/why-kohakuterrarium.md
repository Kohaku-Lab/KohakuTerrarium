# Why KohakuTerrarium exists

## An observation you have probably made

In the last two years a remarkable number of agent products shipped:
Claude Code, Codex, OpenClaw, Gemini CLI, Hermes Agent, OpenCode, and
many more. All of them are genuinely different: different tool
surfaces, different controller loops, different memory strategies,
different multi-agent ideas.

And all of them re-implement the same substrate from scratch:

- a controller that streams from an LLM and parses tool calls
- a tool registry and dispatch layer
- a trigger system for `/loop`, background work, idle checks
- a sub-agent mechanism for context isolation
- input and output plumbing for one or more surfaces
- sessions, persistence, resume
- some form of multi-agent wiring

Every team who wants to try a new agent shape ends up building this
again. That is a lot of code being rewritten just to get to the
actually-interesting part: *the new design*.

## The usual escape, and why it fails

The conventional response is "make one agent so general it handles
every case." This runs into a cliff: the more shapes you cover, the
more special cases you add, and the more brittle the general agent
becomes. A year later somebody has a new idea, the general agent does
not fit it, and they start over.

Chasing general by building a single product is a failed optimisation.

## The actual move

Make **building a purpose-built agent cheap**.

If every new agent shape only costs a config file, a few custom
modules, and a clear mental model, the field stops re-inventing the
wheel. The substrate — the parts that every agent needs and that are
almost identical across them — stays in one place. The genuinely new
part is what you write.

That substrate is what KohakuTerrarium is. A **framework for agents**,
not another agent.

## What "substrate" means here

A concrete list, for calibration:

- A uniform event model. User input, timer fires, tool completion,
  channel messages — all the same envelope.
- The six-module creature abstraction. See
  [what-is-an-agent](what-is-an-agent.md).
- A session layer that is both runtime persistence and a searchable
  knowledge base.
- A multi-agent wiring layer (terrarium) that is purely structural, no
  LLM of its own.
- Python-native composition: every module is a Python class, every
  agent is an async Python value.
- Out-of-the-box runtime surfaces (CLI, TUI, HTTP, WebSocket, desktop,
  daemon) so you do not write transport code.

These are the parts you do not want to rebuild when you want to try a
new agent design.

## What KohakuTerrarium is not

- **Not an agent product.** You do not "run KohakuTerrarium." You run a
  creature built with it. If you want out-of-the-box creatures to try,
  the [`kt-defaults`](https://github.com/Kohaku-Lab/kt-defaults) pack
  is the showcase.
- **Not a workflow engine.** Nothing here assumes your agent follows a
  fixed sequence of steps.
- **Not a general-purpose LLM wrapper.** It does not try to be.

## Positioning, in one sentence

> KohakuTerrarium is the machine for building agents, so people stop
> re-inventing the machine every time they want a new agent.

## See also

- [What is an agent](what-is-an-agent.md) — the definition the framework is built around.
- [Boundaries](../boundaries.md) — when KT fits, when it doesn't.
- [kt-defaults](https://github.com/Kohaku-Lab/kt-defaults) — the showcase creatures + plugin pack.
