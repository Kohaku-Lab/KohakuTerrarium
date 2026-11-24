---
name: browser_act
description: 'Run a batch of browser actions (click, type, select, press, wait, dialog, upload...) by ref. Use it to interact with a page; returns statuses and what changed.'
category: builtin
tags: [browser, web]
---

# browser_act

Runs an ordered list of actions on one tab, stops at the first failure, and
returns one status line per action plus what changed on the page.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| actions | array | yes | Action objects, e.g. `[{"kind": "click", "ref": "e5"}]`; a JSON string or the call body also works |
| files | array | no | File paths that `upload` actions index into |
| tab | string | no | Tab id; default the active tab |

## Behavior

- Put the steps of one intent in one call (fill a form, then submit): later
  actions run only if earlier ones succeeded; the rest show `not executed`.
- The result is a delta: lines added (`+`) or changed (`~`). Read it instead
  of taking a new snapshot; a new page shows its interactive elements.
- Check the result for the outcome you wanted before you report success.

## Output

- `N. <action>: done | failed — reason | dialog pending | not executed`.

## Limits

- At most 20 actions per call; `wait` at most 10 s.

## Reference

### Action kinds

| kind | Fields |
| --- | --- |
| click | `ref`, or `x`/`y` in screenshot pixels; `button`, `double`, `modifiers` |
| type | `ref`; `text` or `secret` (a configured secret name); `submit`, `slowly` |
| select | `ref`, `values` (option labels) |
| check | `ref`, `checked` (default true) |
| press | `key` such as `Enter` or `Control+A`; optional `ref` |
| hover | `ref` |
| scroll | `ref` to scroll into view, or `dx` / `dy` pixels |
| drag | `ref`, `to` (target ref) |
| upload | `ref`, `files` (indexes into the `files` argument) |
| wait | one of `text`, `text_gone`, `seconds` |
| dialog | `accept` (default true), `text` for a prompt |

Coordinates (`x`/`y`) are for canvas-like content with no refs; prefer refs.

### Statuses and what they mean

- **failed — ref eN now points at a different element** (stale ref): the page
  changed since you saw it. Take a snapshot and use the new refs.
- **failed — ref eN is no longer on the page**: the element is gone.
- **dialog pending**: an alert, confirm or prompt opened. Only the `dialog`
  action is accepted until you answer it; `alert` dialogs are accepted for you
  and reported.
- **blocked**: requests the network policy refused are listed under
  `Blocked N requests`; a form that posts to a blocked host does not submit.
- Downloads are saved under `browser-downloads/` in the working directory and
  listed with their path; new tabs a click opened become active and are named.

### Secrets

A `type` action with `secret` types the value of a configured secret; the
value never appears in results. Screenshots and `browser_eval` can still see
what a page shows.
