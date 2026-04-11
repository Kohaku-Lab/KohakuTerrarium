# Composition Algebra

Composition algebra is the programmatic side of KohakuTerrarium's design.

It answers a different question from creature configs and terrariums.

- creatures answer: how do I define one agent?
- terrariums answer: how do I wire multiple agents through channels?
- composition algebra answers: how do I compose agentic steps directly in Python code?

This is an important concept because the framework is not only config-driven. It is also a library for building agentic workflows in code.

## Why this exists

Terrariums are great when you want a runtime world with:

- creature identity
- channels
- persistent collaboration
- topology that exists over time

But sometimes you want tighter control.

Examples:

- a debate loop
- a classifier that routes to specialists
- an iterative write-review-revise loop
- a pipeline that mixes agents and normal Python transforms
- application-owned orchestration where your program is in charge

That is where composition algebra comes in.

## The key mental shift

In config-driven mode, the agent runs itself.

In composition-algebra mode, **your program is the orchestrator**.

The agent becomes a runnable piece inside your code.

That means the control center is not the creature config or terrarium runtime. It is your Python program.

## The main idea

Composition algebra lets you treat agentic steps as composable runnables.

Those runnables can be:

- persistent agents
- ephemeral agent factories
- plain Python functions
- mixed pipelines of both

This makes agentic programs feel closer to a small algebra of transformations than to a monolithic runtime.

## Persistent vs ephemeral

A very important distinction is:

### Persistent agent

A persistent agent keeps context across calls.

Use this when memory across turns matters.

Examples:

- ongoing conversation
- iterative review loop
- debate between stable participants

### Ephemeral agent

An ephemeral agent is created for one task and then discarded.

Use this when each task should be independent.

Examples:

- classification
- one-shot extraction
- disposable specialist worker

This distinction is powerful because it lets you decide where continuity should exist in your workflow.

## Why this is called an algebra

Because the pieces can be composed with simple operators into larger behavior.

You do not need a giant custom orchestration framework just to express:

- do A then B
- do A and B in parallel
- if A fails, try B
- retry A twice
- insert a normal Python transform between two agent steps

That is the algebraic idea: small runnable units, simple operators, larger workflows.

## Relationship to creatures and terrariums

This is not a replacement for creatures or terrariums.

It is another layer of expression.

### Creature

Defines the internal abstraction of a standalone agent.

### Terrarium

Defines a long-lived communication topology between creatures.

### Composition algebra

Defines a code-level orchestration language for agentic steps.

The right one depends on where you want the logic to live.

## When composition algebra is the better fit

Use composition algebra when:

- your application owns orchestration
- you need exact turn ordering
- you want to mix agent steps with normal Python logic
- the workflow is more like a callable graph than a living team
- creating a full terrarium would be heavier than needed

## When a terrarium is the better fit

Use a terrarium when:

- the system should exist as a persistent team
- channel communication is the natural model
- you want topology and runtime observation built in
- the collaboration matters more than exact code-driven sequencing

## A useful comparison

### Terrarium mindset

"These creatures live together in a runtime world and communicate through channels."

### Composition mindset

"My program calls these runnables in a controlled flow."

Both are valuable, but they solve different orchestration problems.

## Composition as a bridge to agent-in-module or agent-in-plugin patterns

Composition algebra also matters conceptually because it explains how agentic logic can appear inside custom modules and plugins.

If an internal helper needs to:

- call a specialist
- route work
- review output
- transform data with an agent in the middle

then that helper logic can be built using the same programmatic composition ideas.

So composition algebra is not only an application-level feature. It also helps explain how the framework can contain agentic internals inside extensions.

## The broader architecture lesson

KohakuTerrarium has more than one composition axis:

- **internal creature composition** through blocks such as tools, triggers, sub-agents, and outputs
- **terrarium composition** through channels between creatures
- **programmatic composition** through algebraic composition in Python

That is one reason the framework can model both:

- long-lived agent systems
- code-owned agent workflows

## Where to learn the practical API

The full practical material lives in:

- [Programmatic Usage](../guides/programmatic-usage.md)
- [Python API](../reference/python.md)
- `examples/code/`

This page is just the mental model.

## Related reading

- [Programmatic Usage](../guides/programmatic-usage.md)
- [Python API](../reference/python.md)
- [Agents](agents.md)
- [Terrariums](terrariums.md)
