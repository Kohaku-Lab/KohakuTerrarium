---
name: write
description: Create a file or replace its entire contents. Requires a prior read if it exists. Not for changing part of a file - use multi_edit.
category: builtin
tags: [file, io]
---

# write

Writes `content` to `path`, creating parent directories as needed.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| path | string | yes | File to write |
| content | string | yes | Complete new contents |

## Behavior

- An existing file must have been read first; the tool refuses otherwise so a
  blind overwrite cannot discard content you never saw.
- The write replaces the whole file. Preserving part of it means reading,
  composing the full new text, then writing.

## Limits

- Text only, written as UTF-8.
