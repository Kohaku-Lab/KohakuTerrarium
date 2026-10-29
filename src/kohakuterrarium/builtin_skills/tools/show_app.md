---
name: show_app
description: 'Show an interactive HTML app (calculator, simulation, small game) in the canvas. Not for plain answers, tables or forms - use text or show_ui.'
category: builtin
tags: [interaction, ui]
---

# show_app

Runs one HTML document as an app in a sandboxed frame in the web UI.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| title | string | to open | App title |
| html | string | to open | Full document or body fragment |
| data | object | no | JSON the app reads as `kt.data` |
| update | string | no | App id to change instead of opening one |
| patch | array | no | With `update`: `{find, replace}` edits |
| read | string | no | App id whose current document to return |
| surface | string | no | `canvas` (default) or `chat` |
| wait_for | array | no | Message names to wait for |
| timeout_s | number | no | Bound the wait |

## Behavior

- Opening returns the app id. `update` with `html` replaces the document,
  with `patch` edits it; each `find` must match exactly once.
- After context compaction, old documents are shortened; call with
  `read=<id>` before patching that app.
- With `wait_for`, the call returns the first matching `kt.send` message, or
  a script error the app hit.

## Limits

- No network except script, style and font loading from the allowed hosts.
- Hidden when no attached client can show apps.

## Reference

### Bridge (inside the app)

| Call | Meaning |
| --- | --- |
| `kt.data` | the `data` passed by the tool |
| `kt.state.get()` / `kt.state.set(patch)` | state kept with the app across reloads |
| `kt.send(name, data)` | a message to you; needs a recent user click in the app |
| `kt.openLink(url)` | open a link after the user confirms |
| `kt.on(name, fn)` | `theme`, `data` and `state` changes from the host |

Theme variables: `--kt-bg`, `--kt-fg`, `--kt-muted`, `--kt-accent`,
`--kt-border`, `--kt-card`, `--kt-font`, `--kt-mono`.

Messages from an app reach you as `widget_event` input marked untrusted when no
call is waiting for them.
