# Multi-chat groups

KohakuTerrarium's chat panel can be split into multiple side-by-side
tab groups, VSCode-style. Each *group* is a full chat surface — its
own tab strip, active tab, transcript, and composer. Drag a tab to
move it between groups; drag a tab to the edge of a group to split.

This is useful when a terrarium hosts several creatures and you want
to watch (or talk to) more than one at a time:

- Pin `developer` to one group while `reviewer` and `tester` scroll
  in adjacent groups.
- Park a `ch:hub` channel transcript beside its producing creature
  to confirm exactly what gets broadcast.
- Drive a `coordinator` while the `searcher` and `analyst` workers
  stream their progress in separate panels.

Multi-chat groups are **always available** — there is no flag, no
opt-in, no toggle. Users who never drag a tab see exactly one group
with one tab strip (visually identical to before this feature), but
the gestures below are live the whole time. Drag a tab to split.

## Splitting

Every chat tab supports two split gestures:

- **Right-click a tab** → *Split right* / *Split down* / *Move to
  new group*. The tab moves into a freshly created sibling group;
  the original group keeps its remaining tabs.
- **Drag a tab** to:
  - another group's tab strip → the tab moves into that group;
  - the *center* of another group's bubble → same (append-to-end);
  - the *left / right / top / bottom* 25 % edge zone of any group's
    bubble → the target group splits in that direction with the
    moved tab in the new sibling.

A drop-zone tint shows up while you hover. Press *Escape* to cancel
a drag mid-motion.

## Closing

- *Close tab* (the `×` on a tab header, or right-click → *Close
  tab*) removes the tab from every group it appears in. If a group
  empties as a result it collapses and the sibling promotes.
- Right-click a group's empty area → *Close group* removes the
  whole group. If only one group remains, *Close group* falls back
  to disabling multi-group mode for this scope.

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| `Ctrl+\` | Split the focused group horizontally (new group on the right). |
| `Ctrl+Alt+\` | Split the focused group vertically (new group on the bottom). |
| `Ctrl+W` | Close the active tab in the focused group. |
| `Ctrl+Shift+W` | Close the focused group (or disable multi-group when only one group remains). |
| `Ctrl+Tab` / `Ctrl+Shift+Tab` | Cycle focus to the next / previous group in tree order. |
| `Ctrl+1` … `Ctrl+9` | Focus the Nth group in tree order. |

Shortcuts are swallowed when the focus is inside a text input or
textarea, so you can still use `Ctrl+W` to delete a word in the
composer (browser default).

## Persistence

The chat-internal split tree is per-scope (one creature / terrarium
instance) and per-machine — it lives in `localStorage` under
`kt.chat.groupTree.<scope>` with a `version: 1` envelope. Resuming
the same session on a different machine starts with the default
single-group layout. The conversation history itself is still
session-scoped (the `.kohakutr` file) and is unaffected by the
display layout.

## Limitations / not supported

- **One WebSocket per scope.** Multiple groups onto the same
  creature share the same stream — splits are purely a display
  projection, never a wire-level fork.
- **Branch view is per-tab, not per-group.** If two groups show the
  same tab, both follow the same branch — same model as VSCode
  "same file in two editor groups". Per-group branch override is
  out of scope for now.
- **Mobile / compact density** keeps the single-group layout. The
  multi-group surface is a desktop affordance only.
- **Floating windows.** No "drag group out into a new window" yet.
- **Cross-group paste / forwarding.** Copy-paste suffices for now.
