# Patterns

None of the patterns on this page required a new framework feature.
They fall out of combining modules that already exist. Every one of
them is a shape you can build today from the six modules, channels,
plugins, and the Python-native substrate.

Use this page as a catalogue, or as proof that the abstractions were
worth keeping small.

## 1. Group chat via tool + trigger

**Shape.** One creature has a `send_message` tool. Another creature
has a `ChannelTrigger` listening on the same channel name. When the
first sends, the second wakes up with a `channel_message` event.

**Why it works.** The channel is just a named queue. The tool writes
to it; the trigger reads from it. Neither module knows about the
other.

**Use when.** You want horizontal multi-agent without importing
`terrarium.yaml` machinery.

**Minimal config.**

```yaml
# creature_a
tools:
  - name: send_message

# creature_b
triggers:
  - type: channel
    options:
      channel: chat
```

## 2. Smart guard via agent-in-plugin

**Shape.** A lifecycle plugin hooks `pre_tool_execute`. Its
implementation runs a small nested `Agent` that reviews the proposed
tool call and returns `allow` / `deny` / `rewrite`. The plugin
returns the rewritten args or raises `PluginBlockError` accordingly.

**Why it works.** Plugins are Python; agents are Python. A plugin can
call an agent just like it can call any async function.

**Use when.** You need policy-based gating that is itself non-trivial
— too complex for a static rule, too domain-specific for a one-size
solution.

## 3. Seamless memory via agent-in-plugin

**Shape.** A `pre_llm_call` plugin runs a tiny retrieval agent. The
retrieval agent searches the session store (or an external vector
DB) for events relevant to the current context, summarises hits, and
prepends them as system messages. The outer creature's prompt
quietly gets richer without any tool call.

**Why it works.** The creature never has to decide "should I retrieve
something now" — the plugin always does, and the LLM sees the result
in every turn.

**Use when.** RAG-style memory is useful but you do not want the
main agent to spend tool budget on it.

## 4. Adaptive watcher via agent-in-trigger

**Shape.** A custom trigger whose `fire()` body runs a small judging
agent on a timer. The agent inspects the current world state and
returns a `fire / don't fire` decision. If it fires, an event goes
to the outer creature.

**Why it works.** Triggers are just async generators of events. What
the generator looks at is up to you, and "an embedded mini-agent" is
one legal option.

**Use when.** A fixed interval is too coarse, a fixed rule is too
brittle, but a full LLM turn per-tick is affordable.

## 5. Silent controller + external sub-agent

**Shape.** A creature's controller produces no user-facing text —
only tool calls and a final sub-agent dispatch. The sub-agent is
configured with `output_to: external`, so *its* text streams to the
user while the parent stays invisible.

**Why it works.** Output routing treats the sub-agent stream as
peer-equal to the controller's. You pick which one the user sees.

**Use when.** You want a specialist voice (persona, formatting,
constraints) to be the user-facing one, with the orchestrator behind
the curtain. Many of the kt-defaults chat creatures use this.

## 6. Tool-as-state-bus

**Shape.** Two creatures cooperating in a terrarium both use the
shared environment's scratchpad-like channels as a rendezvous: one
writes a `tasks_done: 3` record; the other polls it. Or they use the
`scratchpad` tool via a shared session key.

**Why it works.** Sessions and environments already have KV storage.
Tools expose them to the LLM.

**Use when.** You need coarse-grained coordination without imposing a
message-passing protocol.

## 7. Mixed-axis multi-agent

**Shape.** A terrarium whose root (or whose creatures) themselves
use sub-agents internally. Horizontal at the top level; vertical per
creature.

**Why it works.** Sub-agents and terrariums are orthogonal. Nothing in
the framework forbids using both.

**Use when.** The team has roles, and some roles internally benefit
from decomposition (plan → implement → review) that does not need to
be visible as separate creatures.

## 8. Inline control via framework commands

**Shape.** Inside a turn, the controller emits small inline directives that talk to the framework: the `info` command loads a tool's full docs on demand, `read_job` reads partial output from a running background tool, `jobs` lists pending work, `wait` blocks on a stateful sub-agent. These run inline — no new LLM round-trip.

The syntax is whichever `tool_format` the creature is configured with; in the default bracket form, a command call looks like `[/info]tool_name[info/]`.

**Why it works.** Framework commands are a parser-level affordance,
not tools. They cost nothing to invoke.

**Use when.** You want the LLM to inspect its own state mid-turn
without burning a tool slot on it.

## Not a closed set

The point of this page is not the patterns themselves. It is that
small, composable modules produce useful shapes you do not have to
hard-code. If a pattern here is close to what you need, the tweak
probably fits within the same building blocks. If you invent a new
one, open a PR against this file.

## See also

- [Agent as a Python object](python-native/agent-as-python-object.md)
  — the property that makes 2–4 possible.
- [Tool](modules/tool.md), [Trigger](modules/trigger.md),
  [Channel](modules/channel.md), [Plugin](modules/plugin.md) — the
  building blocks these patterns combine.
- [Boundaries](boundaries.md) — the abstraction is a default, not a
  law; patterns sometimes cross the default intentionally.
