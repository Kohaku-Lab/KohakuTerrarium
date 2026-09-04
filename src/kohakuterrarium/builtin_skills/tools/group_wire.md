---
name: group_wire
description: "Create or delete output wires that deliver a creature's turn-end text to another. Not for channels - use group_channel."
category: builtin
tags: [group]
---

# group_wire

Manages output wiring: an edge that delivers one creature's turn-end output to
another automatically.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| action | string | yes | `add` or `remove` |
| source | string | yes | Creature whose output is delivered |
| target | string | yes | Creature (or `root`) that receives it |
| with_content | boolean | no | Default true; false sends a lifecycle ping only |

## Behavior

- Wiring suits an unconditional hand-off. A conditional one belongs on a
  channel, because a wire cannot branch.
- Arrivals are tagged `[output-wire from <source>]`.
