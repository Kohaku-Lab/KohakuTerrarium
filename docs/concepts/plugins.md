# Plugins and Extensibility

KohakuTerrarium has two major ways to customize behavior:

- **custom modules** customize the **blocks** in the creature abstraction
- **plugins** customize the **connections** between those blocks

That distinction is one of the most important architectural ideas in this codebase.

## Start from the creature diagram

A creature is made of blocks:

```text
    List, Create, Delete  +------------------+
                    +-----|   Tools System   |
      +---------+   |     +------------------+
      |  Input  |   |          ^        |
      +---------+   V          |        v
        |   +---------+   +------------------+   +------------+
        +-->| Trigger |-->|    Controller    |-->| Sub Agents |
User input  | System  |   |    (Main LLM)    |<--| with tools |
            +---------+   +------------------+   +------------+
                ^             |          |
                |             v          v
                |         +--------+  +------+
                +---------|Channels|  |Output|
                 Receive  +--------+  +------+
```

This diagram gives you the right mental model for extensibility.

## Custom modules customize the blocks

When you create a custom:

- input
- output
- tool
- trigger
- sub-agent

you are replacing or extending one of the concrete blocks in the creature.

That means a custom module answers this kind of question:

- what is the input block for this creature
- what kind of output block should it have
- what tools exist in the tools block
- what trigger block wakes it up
- what kind of sub-agent block can it delegate to

So custom modules are **block customization**.

## Plugins customize the connections

Plugins are different.

They do not primarily replace a block.
They customize the **connections between blocks**.

Examples:

- the connection between controller and LLM call
- the connection between controller and tool execution
- the connection between event flow and prompt building
- the connection between sub-agent dispatch and result handling
- the connection between output generation and output delivery

So plugins are **connection customization**.

That is the key idea.

If modules define the parts of the creature, plugins define how the parts interact.

## Why this distinction is powerful

A lot of frameworks only let you customize the blocks.

That means if you want something advanced, you often end up:

- replacing too much
- forking the runtime
- writing special-case logic inside the controller
- turning one module into a giant god object

KohakuTerrarium gives you a second axis.

You can customize:

- the blocks themselves
- the connections between the blocks

That makes the framework much more expressive.

## A simple way to think about it

### Module question

"What component should live here?"

Examples:

- what tool should the agent have
- what input module should receive events
- what output module should deliver results

### Plugin question

"How should these components interact?"

Examples:

- should tool calls be filtered or rewritten first
- should extra context be injected before the LLM call
- should sub-agent output be inspected before re-entering the controller
- should model choice adapt to the current task

## Both modules and plugins can be agentic

This is another key idea.

Both custom modules and plugins can run **agentic logic** inside themselves, because KohakuTerrarium can be called programmatically.

That means a module or plugin does not have to be simple or static.
It can internally call an `Agent`, an `AgentSession`, a `TerrariumRuntime`, or other programmatic runtime surface.

So:

- a custom tool can internally run an agent
- a custom trigger can use an agent to decide whether an event should fire
- a custom sub-agent can itself orchestrate more specialized internal logic
- a plugin can call an agent before allowing a tool execution
- a plugin can use an agent to decide what context to inject before an LLM call

This is where the system becomes very powerful.

## Agent inside a custom module

Example mental models:

### Custom tool with internal agent

A tool might:

- accept a user task
- spin up a specialist agent programmatically
- let that agent do structured work
- return only the distilled result to the main creature

In that case, the tool is still the **block** being customized, but the block itself contains agentic internals.

### Custom trigger with internal agent

A trigger might:

- monitor incoming external state
- use an agent to interpret whether the change matters
- only fire an event when the agent decides it is worth attention

Again, this is still block customization, but now the block has intelligence inside it.

## Agent inside a plugin

Plugins can do something slightly different.

Because they sit on the connections, an internal agent can be used to judge or reshape a flow.

Examples:

### Adaptive memory plugin

A plugin on the path into the LLM call could:

- inspect the current turn
- decide whether memory retrieval is needed
- call an internal agent to rank or summarize memory candidates
- inject only the most useful memory context into the message flow

This is not just storage. It is a dynamic intelligence layer on the connection into the controller's LLM interaction.

### Safety or policy plugin

A plugin on the path to tool execution could:

- inspect a bash command
- call a reviewer or safety agent programmatically
- decide whether to block, rewrite, or allow the command

This is connection-level intelligence.

### Routing plugin

A plugin before the LLM call could:

- analyze the incoming task
- run a lightweight classifier agent
- choose a better model or behavior profile for the next turn

Again, the block is still the controller. The plugin is changing the connection into it.

## Why "connection customization" matters more than it sounds

This is what lets you build sophisticated behavior without turning the core agent abstraction into mush.

If you tried to solve everything by changing blocks only, you would end up overloading:

- the controller
- the tools
- the prompt system

Instead, plugins let you insert logic at the interaction boundaries.

That keeps the architecture cleaner:

- blocks remain understandable
- connections become programmable
- advanced behavior becomes composable

## Relationship to prompt plugins and lifecycle plugins

KohakuTerrarium has plugin behavior in more than one place, but conceptually they are the same architectural move.

### Prompt plugins

Prompt plugins customize the connection into the final system prompt.

They shape what context the controller sees before reasoning begins.

### Lifecycle plugins

Lifecycle plugins customize runtime connections such as:

- before and after LLM calls
- before and after tool execution
- before and after sub-agent execution
- event processing and interruption points

Both are connection customization.

## The deeper pattern

A helpful summary is:

- **modules customize the nodes**
- **plugins customize the edges**
- **both can contain agentic logic internally**

That is a strong and unusual architecture.

It means the framework is not limited to static wiring.
It can support adaptive and reflective behavior at multiple levels.

## When to choose what

### Choose a custom module when

You need a new or replaced capability block.

Examples:

- a new tool
- a different input source
- a custom output target
- a specialized trigger
- a custom sub-agent implementation

### Choose a plugin when

You need to change how existing blocks interact.

Examples:

- inject context before the LLM call
- enforce tool policy
- add adaptive memory behavior
- inspect or reshape sub-agent flows
- do model routing or dynamic behavior control

## Related reading

- [Custom Modules](../guides/custom-modules.md)
- [Plugins Guide](../guides/plugins.md)
- [Prompt System](prompts.md)
- [Agents](agents.md)
- [Programmatic Usage](../guides/programmatic-usage.md)
