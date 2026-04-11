# Concepts Overview

Concepts are where KohakuTerrarium defines what each thing is.

That means a good concept page should answer questions like:

- what is this thing
- what boundary does it own
- what kind of composition does it represent
- what is it not
- how does it relate to the other concepts

This matters because KohakuTerrarium is not just a bag of features. It is a framework with a specific way of defining agent systems.

## The central idea

KohakuTerrarium separates several different kinds of structure that many systems blur together.

At a high level, the framework defines:

- what a standalone agent is
- how agent internals are composed
- how multiple agents are wired together
- how runtime boundaries are isolated
- how extensibility works at both block level and connection level
- how programmatic composition differs from runtime topology

That separation is the heart of the architecture.

## The major concepts

## 1. Creature

See [Agents](agents.md).

A **creature** is the framework's definition of a standalone agent.

It is a complete unit with its own:

- controller
- tools
- sub-agents
- triggers
- input and output
- prompts
- session-scoped state

A creature defines the **internal composition** of an agent.

### A creature is

- a self-contained agent abstraction
- the place where agent identity and behavior live
- the owner of internal orchestration

### A creature is not

- a multi-agent team
- just a prompt
- just a tool list
- just a workflow node

## 2. Terrarium

See [Terrariums](terrariums.md).

A **terrarium** is the framework's definition of a multi-agent wiring layer.

It connects creatures through channels, manages lifecycle, and provides topology and observation.

A terrarium defines **external composition between creatures**.

### A terrarium is

- a topology
- a collaboration layer
- a runtime world for creatures

### A terrarium is not

- another reasoning agent
- the place where creature internals are defined
- a replacement for the creature abstraction

## 3. Channels

See [Channels](channels.md).

A **channel** is the communication primitive between creatures.

Channels are how messages move across the terrarium-level topology.

### A channel is

- an explicit communication path
- either queue-like or broadcast-like
- part of the wiring layer

### A channel is not

- a creature internal
- an implicit shared mind
- a hidden coupling mechanism

## 4. Environment and Session

See [Environment and Session](environment.md).

These define runtime boundaries.

- **Environment** is shared runtime state at the collaboration level
- **Session** is private runtime state at the creature level

This separation defines **where state is shared and where it is isolated**.

### Environment is

- shared terrarium-facing state
- the home of shared channels and common runtime context

### Session is

- creature-private operational state
- the home of scratchpad and other local runtime state

### They are not

- the same thing
- interchangeable storage scopes

## 5. Tool formats

See [Tool Formats](tool-formats.md).

Tool formats define how the model expresses actions.

This matters because the same creature abstraction can work with different model capabilities and invocation styles.

### Tool formats are

- the action-call surface between model output and runtime execution
- part of the controller-to-tool interaction contract

### Tool formats are not

- the tool system itself
- the full execution model

## 6. Modules

See [Custom Modules](../guides/custom-modules.md).

Modules are the framework's way of defining the major blocks inside the creature abstraction.

These include:

- input
- output
- tool
- trigger
- sub-agent

A module defines **what kind of block exists in the creature**.

### Modules are

- block-level customization
- capability-level extension points

### Modules are not

- the connection logic between blocks

## 7. Plugins

See [Plugins and Extensibility](plugins.md).

Plugins are the framework's way of defining how blocks interact.

If modules customize the nodes of the creature, plugins customize the edges between those nodes.

A plugin defines **connection-level customization**.

### Plugins are

- connection-level behavior
- interception, transformation, policy, and adaptive layers

### Plugins are not

- replacements for all modules
- just logging hooks

## 8. Composition algebra

See [Composition Algebra](composition-algebra.md).

Composition algebra defines programmatic composition in Python.

It exists alongside creatures and terrariums, but it is not the same as either.

### Composition algebra is

- application-owned orchestration in code
- composition of agentic runnables and transforms

### Composition algebra is not

- the creature abstraction itself
- the terrarium topology model

## Three different composition axes

One reason KohakuTerrarium is powerful is that it supports more than one kind of composition.

### Internal composition

Inside a creature.

This is where controller, tools, triggers, sub-agents, inputs, and outputs fit together.

### Topological composition

Across creatures in a terrarium.

This is where channels, collaboration, and team structure live.

### Programmatic composition

Inside Python code through composition algebra.

This is where your application becomes the orchestrator.

These are related, but they are not the same thing.

## Why the extensibility model is special

KohakuTerrarium does not only let you customize blocks.
It also lets you customize connections.

That gives the system two distinct extension layers:

- **modules customize blocks**
- **plugins customize connections**

And both can themselves contain agentic logic because the runtime can be called programmatically.

That means:

- a custom tool can internally run an agent
- a custom trigger can use an agent to decide whether to fire
- a plugin can use an agent to decide what context to inject
- a plugin can use an agent to decide whether a tool call should proceed

This is one of the reasons the framework can support sophisticated adaptive behavior without collapsing everything into one giant controller.

## A compact mental model

If you want one short summary, use this:

- **creature** defines what one agent is
- **terrarium** defines how multiple creatures are wired together
- **channels** define how creatures communicate
- **environment and session** define where state is shared or isolated
- **modules** define the major blocks in a creature
- **plugins** define the connections between those blocks
- **composition algebra** defines programmatic orchestration in Python

## Where to go next

- [Agents](agents.md)
- [Terrariums](terrariums.md)
- [Channels](channels.md)
- [Plugins and Extensibility](plugins.md)
- [Composition Algebra](composition-algebra.md)
- [Environment and Session](environment.md)
