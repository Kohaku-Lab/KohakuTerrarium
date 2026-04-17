# Concepts

Concept docs teach mental models. They are not a reference — field names,
signatures, and commands live in [reference/](../reference/README.md).
They are not a guide — task-by-task instructions live in
[guides/](../guides/README.md). They exist to make the rest of the
docs *feel obvious* by the time you touch them.

If a concept doc ever reads like "here is a list of classes," something
has gone wrong. Tell us in an issue.

## Reading paths

You do not have to read these in order. Pick the path that matches why
you are here.

### Evaluator (20 minutes)

You want to know what this framework is and whether it's for you.

1. [Why KohakuTerrarium](foundations/why-kohakuterrarium.md)
2. [What is an agent](foundations/what-is-an-agent.md)
3. [Composing an agent](foundations/composing-an-agent.md)
4. [Boundaries](boundaries.md)

### Builder (1 hour)

You want to build a creature that is not in `kt-defaults`.

1. Evaluator path above
2. [Modules overview](modules/README.md) → read each module doc as needed
3. [Agent as a Python object](python-native/agent-as-python-object.md)
4. [Patterns](patterns.md)
5. [Composition algebra](python-native/composition-algebra.md) *(if you want to glue agents together programmatically)*

### Multi-agent user

You want to run a team of creatures.

1. Start from the builder path.
2. [Multi-agent overview](multi-agent/README.md)
3. [Terrarium](multi-agent/terrarium.md)
4. [Root agent](multi-agent/root-agent.md)
5. [Channel](modules/channel.md) *(the primitive both rely on)*

### Contributor / deep read

You want to change the framework itself.

1. Everything in foundations.
2. Every module doc.
3. Every impl-note in [impl-notes/](impl-notes/README.md).
4. Then [`dev/internals.md`](../dev/internals.md) in the dev section.

## Structure

```
concepts/
├── foundations/         Why this exists; what an agent is; how one is composed.
├── modules/             One doc per module of a creature.
├── python-native/       Agents as Python values; composition algebra.
├── multi-agent/         Terrarium + root agent.
├── impl-notes/          Specific implementation choices worth teaching.
├── patterns.md          What emerges from combining modules.
├── boundaries.md        The abstraction is a default, not a law.
└── glossary.md          Plain-English one-paragraph definitions.
```

If a term stops you mid-read, the [glossary](glossary.md) is the
fastest lookup.

## Promises the concept docs try to keep

- **Derivations, not lists.** Every module earns its seat.
- **No module is mandatory.** Each doc closes with *don't be bounded*.
- **Honest about rough parts.** Where the framework is experimental,
  docs say so and link the [ROADMAP](../../ROADMAP.md).
- **Teaching, not cataloguing.** For the catalogue, see
  [reference/](../reference/README.md).

## See also

- [Guides](../guides/README.md) — task-oriented how-tos.
- [Reference](../reference/README.md) — exhaustive lookup for commands, APIs, fields.
- [Development](../dev/README.md) — contributor-facing internals.
