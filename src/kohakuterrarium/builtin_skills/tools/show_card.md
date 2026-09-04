---
name: show_card
description: 'Display a styled card, optionally with buttons, and return the clicked action. Not for free-text answers - use ask_user.'
category: builtin
tags: [interaction, ui]
---

# show_card

Renders a structured panel and, when it has buttons, waits for the click.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| title | string | yes | Card heading |
| body | string | no | Markdown body |
| fields | array | no | `{label, value}` pairs |
| actions | array | no | `{id, label, style?, url?}` buttons |
| wait_for_reply | boolean | no | Defaults true when non-link actions exist |
| timeout_s | number | no | Bound the wait |

## Behavior

- With non-`link` actions it blocks and returns the chosen action id; with no
  actions it displays and returns at once.
- `link` actions only open a URL, so a link-only card never waits.
- With no UI attached it returns a plain-text rendering instead of blocking.

## Limits

- Offers a choice among the actions you define. Free text is `ask_user`.

## Reference

### Action shape

```json
{"id": "approve", "label": "Approve", "style": "primary"}
{"id": "docs", "label": "Open docs", "url": "https://example.com"}
```

`style` is one of `primary`, `secondary`, `danger`. An action with `url` is a
link and never returns a click.
