---
name: show_card
description: Display a styled card with optional action buttons
category: builtin
tags: [interaction, ui, output-event]
---

# show_card

Render a styled "card" — a structured UI block with a title, optional
body / fields / footer, and optional action buttons that the user can
click. Cards are richer than plain text and lighter than `ask_user` /
`selection`: a great fit for plan previews, sub-agent result
summaries, cost / status panels, "approve plan?" gates, and small
N-button surveys.

Cards render natively in the web frontend (Element Plus card),
TUI (Textual block + Buttons), and Rich CLI (rich.Panel). When the
user has no UI attached, the tool falls back to a plain-text
rendering so the model still gets a useful result.

## WHEN TO USE

`show_card` is for **structured display + button choices**. It's
the right tool when the user shouldn't have to _type_ the answer:
they read a styled card and click one of 2–4 buttons.

| Need                                                     | Tool                                     |
| -------------------------------------------------------- | ---------------------------------------- |
| Free-text answer (a name, a path, a description)         | **ask_user**                             |
| Pick from N labelled options ("Approve / Edit / Reject") | **show_card** with `actions` (this tool) |
| Display structured info beautifully                      | **show_card** display-only (this tool)   |

Use `show_card` when:

- Display a structured summary the user should _see_ prominently —
  e.g. "Plan ready", "Migration complete", "Cost so far".
- Approve / reject a single decision with 2-4 button options.
- Present a survey-style choice between named options. Each click
  returns a stable `action_id` you can branch on — no string parsing
  required.
- Surface key/value facts together (file count, line count, risk,
  duration) without writing them as prose.
- Direct the user to docs / external resources via a `link`-styled
  action (opens URL in their browser, no agent round-trip).

Use `ask_user` instead when:

- The answer is genuinely free-form text — a name, a path, a
  description, a reason. `show_card` buttons can't capture that.

Cards top out at ~4 actions before they get cluttered. For
pick-one-of-many-options-from-a-long-list, dispatch through a
sub-agent or write a richer protocol — don't stuff 12 buttons into
a card.

## HOW TO USE

```
tool call: show_card(
title: "Migration plan",
accent: "warning",
subtitle: "5 files, ~200 lines",
body: |
  1. Move auth middleware into `app/auth/`.
  2. Update existing imports in 12 places.
  3. Run the schema migration.
fields:
  - {label: "Files", value: "5", inline: true}
  - {label: "Lines", value: "~200", inline: true}
  - {label: "Risk",  value: "Medium"}
actions:
  - {id: "approve", label: "Approve", style: "primary"}
  - {id: "edit",    label: "Edit",    style: "secondary"}
  - {id: "reject",  label: "Reject",  style: "danger"}
)
```

## Arguments

| Arg            | Type       | Description                                                                                                      |
| -------------- | ---------- | ---------------------------------------------------------------------------------------------------------------- |
| title          | str        | Card header (required)                                                                                           |
| subtitle       | str        | Optional smaller header line                                                                                     |
| icon           | str        | Optional emoji shown next to title                                                                               |
| accent         | str        | One of `primary`, `info`, `success`, `warning`, `danger`, `neutral`. Renderers map to a semantic colour.         |
| body           | str        | Optional markdown body — supports code fences, lists, links.                                                     |
| fields         | list[dict] | Optional key/value rows. Each entry: `{label, value, inline?}`. `inline=true` packs into a 2-column grid.        |
| footer         | str        | Optional small italic line at the bottom.                                                                        |
| actions        | list[dict] | Optional buttons. Each: `{id, label, style?, url?}`. `style` ∈ `primary`, `secondary`, `danger`, `link`.         |
| wait_for_reply | bool       | Whether to block for the user's button click. Defaults to `true` when `actions` is non-empty, `false` otherwise. |
| timeout_s      | float      | Optional wait timeout. Default `null` = wait forever.                                                            |
| surface        | str        | `chat` (default) or `modal`.                                                                                     |

## Output Format

- **Display-only** (no actions, or `wait_for_reply=false`): returns
  `card displayed`.
- **Interactive**: returns `action: <action_id>` (or `action: <id>`
  + the submitted values on a second line if any).
- **Timeout**: returns `card timed out without reply`.
- **No router attached**: returns a plain-text rendering of the card
  so the model still sees the content it tried to display.

## Examples

Plan preview with approve/reject:

```
tool call: show_card(
title: "Refactor plan",
accent: "primary",
body: |
  Move authentication into a new module so the existing
  `app/handlers.py` no longer pulls in legacy session code.
actions:
  - {id: "go",   label: "Run plan", style: "primary"}
  - {id: "no",   label: "Cancel",   style: "secondary"}
)
```

A status / progress card (display-only):

```
tool call: show_card(
title: "Migration done",
icon: "✓",
accent: "success",
fields:
  - {label: "Files migrated", value: "47", inline: true}
  - {label: "Duration",       value: "12s", inline: true}
)
```

A small survey:

```
tool call: show_card(
title: "Where should I commit this fix?",
accent: "info",
actions:
  - {id: "main",     label: "main branch",       style: "primary"}
  - {id: "feature",  label: "new feature branch", style: "secondary"}
  - {id: "draft",    label: "leave as draft",    style: "secondary"}
)
```

A docs link (no agent round-trip):

```
tool call: show_card(
title: "More info",
body: "See the design doc for the full proposal.",
actions:
  - {id: "doc", label: "Open docs", style: "link", url: "https://example.com/doc"}
)
```

## LIMITATIONS

- Maximum sensible action count is ~4. Renderers may visually
  truncate beyond that.
- `actions` with `style: "link"` open the URL externally and do NOT
  submit a reply. Use a normal action style when you want to know
  whether the user clicked.
- The CLI renders cards as Rich panels — clicking is not supported
  in CLI; use TUI or web for interactive cards.
- For multi-field input forms (multiple text inputs, dropdowns),
  cards are not the right primitive — use `ask_user` per field or
  a future `form` event kind.

## TIPS

- Keep the body short. If you need long prose, just write text —
  cards work best for at-a-glance info.
- Use `inline: true` on related small fields so they pack
  side-by-side; leave it off for important standalone facts.
- Pick an `accent` that matches the meaning: `success` for
  completion, `warning` for "please review", `danger` for
  destructive, `info` for neutral status.
- For "looks like an approval gate but the agent really wants a
  free-text reason," combine: emit the card display-only, then
  call `ask_user` for the reason.
