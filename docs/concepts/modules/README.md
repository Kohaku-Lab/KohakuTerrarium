# Modules

A creature is made of modules. This section has one concept doc per
module. They all follow the same six-part shape:

1. **What it is** — the conceptual role.
2. **Why it exists** — what breaks without it.
3. **How we define it** — the contract.
4. **How we implement it** — shape of the built-in implementation + key invariants.
5. **What you can therefore do** — the obvious uses and the surprising ones.
6. **Don't be bounded** — this module is a default, not a law.

The six modules derived in [what-is-an-agent](../foundations/what-is-an-agent.md):

- [Controller](controller.md) — the reasoning loop.
- [Input](input.md) — the first trigger.
- [Trigger](trigger.md) — world-to-agent wake-up.
- [Output](output.md) — agent-to-world delivery.
- [Tool](tool.md) — the agent's hands.
- [Sub-agent](sub-agent.md) — a context-scoped delegate.

Plus four cross-cutting pieces that are not in the canonical six but
behave exactly like modules:

- [Channel](channel.md) — communication substrate.
- [Plugin](plugin.md) — modifies the connections between modules.
- [Session and environment](session-and-environment.md) — private vs
  shared state.
- [Memory and compaction](memory-and-compaction.md) — session as a
  searchable knowledge base, plus non-blocking compaction.

You do not have to read these in order. Skim the modules you will use;
skip the rest.
