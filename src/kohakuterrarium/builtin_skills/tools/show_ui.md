---
name: show_ui
description: 'Show an interactive widget (form, table, chart, calculator) built from a JSON spec. Not for plain answers - write text.'
category: builtin
tags: [interaction, ui, widget]
---

# show_ui

Opens a `kt.ui` widget in the chat (or the canvas) and can wait for what it
sends back.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| spec | object | yes* | `{title, layout, state, computed, root}`; *not with `update` alone |
| update | string | no | Widget id to change: a new `spec`, or `state` to merge |
| state | object | no | With `update`: values merged into the widget state |
| surface | string | no | `chat` or `canvas`; `layout: "page"` defaults to `canvas` |
| wait_for | array | no | Message names to wait for; returns the first |
| timeout_s | number | no | Bound the wait |

## Behavior

- An invalid spec returns every problem with its JSON path; fix and call again.
- Inputs `bind` a `state` key; `computed` values re-evaluate when state changes.
- An `update` with a new spec keeps the current values of keys it still has;
  pass `state` to override them.
- A button or form `send` resolves a matching `wait_for`, or reaches you later
  as a `widget_event` trigger.
- With no UI attached it returns the widget as text.

## Limits

- Spec ≤ 64 KB; expressions ≤ 2 KB; `each` draws ≤ 500 items.
- Images: `data:image/...` or same-origin paths only.

## Reference

### Spec

```json
{
  "title": "Bill splitter",
  "state": {"total": 120, "people": 3, "tip": 10},
  "computed": {"each": "round(total * (1 + tip / 100) / people, 2)"},
  "root": {"type": "stack", "children": [
    {"type": "number", "label": "Total", "bind": "total"},
    {"type": "slider", "label": "Tip %", "bind": "tip", "min": 0, "max": 30},
    {"type": "stat", "label": "Each pays", "value": "{{ fmt(each, 2) }}"},
    {"type": "button", "label": "Send", "send": "result", "data": {"each": "{{ each }}"}}
  ]}
}
```

A string that is exactly `{{ expr }}` keeps the value's type. Every node takes
`visible` (bool or template) and `alt` (text for plain-text clients).

### Components

- Layout: `stack` (direction, gap, wrap, align), `grid` (columns), `card`
  (title, subtitle), `section` (title), `tabs` of `tab` (label), `divider`.
- Content: `text` (markdown value), `heading` (level 1-4), `badge` (tone),
  `stat` (label, value, hint), `kv` (fields `[{label, value}]`), `list`
  (items, ordered), `table` (columns `[key | {key, label, align}]`, rows of
  objects or arrays), `image` (src, alt, width), `code` (value, lang).
- Data: `chart` (kind bar/line/area/pie/scatter, labels, series
  `[{name, data}]`), `diagram` (source).
- Inputs (all `bind`, `label`): `number`/`slider` (min, max, step),
  `text_input` (placeholder, multiline), `select`/`radio` (options: strings or
  `{value, label}`), `toggle`, `checkbox`, `date`.
- Actions: `button` (label, style, `set: {key: expr}`, `send`, `data`,
  disabled), `form` (children, `send`, submit label; sends the values its
  inputs bind). A button with both `set` and `send` evaluates both against
  the values before the click.
- Repeat: `each` (`items` expression, `as` name, `child`; adds `index`).

### Expressions

Numbers, strings, `true false null`, lists `[a, b]`; names from `state`,
`computed` and `each`; `.key` and `[i]`; `+ - * / % **`, comparisons,
`and or not`, `a ? b : c`. Functions: `round floor ceil abs min max sum avg
len pow sqrt clamp fmt pct range join keys values str num`. No object
literals, no assignment; a missing key reads as `null`.

### Messages

`wait_for: ["result"]` returns `message result: {"each": 44}`. Without a
waiter the same message arrives as
`<widget_event widget="w_…" name="result" …>{json}</widget_event>`.
