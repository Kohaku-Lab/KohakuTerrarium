# What is an agent

An agent, in KohakuTerrarium, is a **creature**. To see why a creature
looks the way it does, it helps to build one up from scratch.

## Stage 0 — Chat bot

```
    +-------+      +-----+      +--------+
    | Input | ---> | LLM | ---> | Output |
    +-------+      +-----+      +--------+
```

A user types, the model answers, the answer is printed. This is as
simple as conversational AI gets. It can *say things*. It cannot *do
things*.

## Stage 1 — Add a tools system

```
                 +--------------+
                 | Tools System |
                 +------+-------+
                        ^  |
                        |  v
    +-------+      +---------+      +--------+
    | Input | ---> |   LLM   | ---> | Output |
    +-------+      +---------+      +--------+
```

Give the LLM the ability to call tools: shell commands, file edits,
web searches. Now it can act. It is no longer a chat bot; it is an
agent in the smolagent or swe-agent sense.

This is already useful. It is also already limited: the only way to
make this agent do anything is to *type at it*.

## Stage 2 — Add a trigger system

```
                                +--------------+
                           +--> | Tools System |
                           |    +--------------+
                           |           ^  |
                           |           |  v
    +-------+   +---------+v     +---------+      +--------+
    | Input |-->| Trigger |----->|   LLM   | ---> | Output |
    +-------+   | System  |      +---------+      +--------+
                +---------+
```

Real agents need to wake themselves up without user input: a `/loop`
feature, a background job finishing, an idle check, a webhook, a timer.
Something has to watch the world and fire the controller when
conditions are met. That something is a **trigger**.

Once you have triggers, you notice that *user input itself is just
another kind of trigger*. So "input" becomes a specific case of a more
general wake-up mechanism. Claude Code and OpenClaw sit here.

## Stage 3 — Add sub-agents

```
                                +--------------+
                           +--> | Tools System |
                           |    +--------------+
                           |           ^  |
                           |           |  v
    +-------+   +---------+v     +---------+      +--------+
    | Input |-->| Trigger |----->|   LLM   | ---> | Output |
    +-------+   | System  |      +---------+      +--------+
                +---------+            ^  |
                                       |  v
                                 +--------------+
                                 |  Sub Agents  |
                                 +--------------+
```

Context windows are finite. A long task with many exploratory sub-steps
will blow the budget if every sub-step lands in the parent's
conversation.

The fix is to spawn a **sub-agent**: a nested creature that runs with
its own context, reports back a condensed result, and disappears.
Importantly, a sub-agent is *also just a tool* from the parent's
perspective — you call it, it returns something. Modern coding agents
all land here.

## The six-module creature

Putting it together:

| Module | Role |
|---|---|
| **Controller** | The reasoning loop. Streams from an LLM, parses tool calls, dispatches them. |
| **Input** | Tells the controller what to do (one specific kind of trigger). |
| **Trigger** | Fires the controller when the world demands it. |
| **Tool** | What the agent uses to do things. |
| **Sub-agent** | A nested creature — conceptually also a tool. |
| **Output** | How the agent talks back to its world. |

A **creature** is the union of those six modules. This is the
first-class abstraction in KohakuTerrarium, and it is what every guide,
every reference, and every other concept doc is ultimately about.

One clarification worth making up front: when you see the root README
or guides talk about "five module types" that you can extend, they
mean the five user-swappable ones — Input, Trigger, Output, Tool,
Sub-agent. The sixth module, the Controller, is the reasoning loop
the framework provides; you configure it (LLM, skill mode, tool
format) rather than swap its implementation. Same six modules, one
note about who implements which.

## Don't be bounded by the derivation

The story above is a useful default, not a law. In practice:

- Trigger-only creatures skip input entirely (`input: none`). A
  scheduled-job agent works fine without a user.
- Output-less creatures are legitimate (side-effects-only).
- Tool-less creatures can exist (a pure response agent).
- The framework itself bends the abstraction when convenient: a
  terrarium channel is technically a "tool writes; trigger fires in
  another creature" pattern that does not cleanly belong to either
  module. That's fine. See [boundaries](../boundaries.md).

## See also

- [Composing an agent](composing-an-agent.md) — how the six modules
  actually wire up at runtime.
- [Modules](../modules/README.md) — one doc per module, deep.
- [Boundaries](../boundaries.md) — the abstraction as a default.
