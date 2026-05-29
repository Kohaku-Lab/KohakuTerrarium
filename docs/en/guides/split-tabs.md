# Splitting tabs side-by-side

The desktop dashboard's main area can be split into multiple
**tab-groups**, VSCode-style. Each group is a full tab strip with its
own active tab, and groups can host *any* kind of tab — so you can put
a chat next to a code editor next to the dashboard, all visible at once.

Splitting is **always available** — there is no flag or toggle. If you
never split, you see exactly one group with one tab strip (identical to
before this feature), but the gestures below are live the whole time.

> Splitting applies to the desktop / wide layout. On the compact
> (mobile) shell tabs stay a single strip.

## Splitting a group

- **Drag a tab to the edge of any group's body** — the *left / right /
  top / bottom* 25 % edge zone — and that group splits in the
  corresponding direction, with the dragged tab landing in the new
  sibling group. A drop-zone tint shows where the split will land.
- **Keyboard:** `Ctrl+\` splits the focused group horizontally (new
  group on the right); `Ctrl+Alt+\` splits it vertically (new group on
  the bottom).

The focused group is outlined; click anywhere in a group to focus it.
New tabs (the `+` menu, or opening a creature / session) open into the
focused group.

## Moving tabs between groups

- Drag a tab onto **another group's tab strip**, or the **center** of
  another group's body, to move it into that group.
- Drag a tab **within its own strip** to reorder it.

When a group loses its last tab it collapses automatically and its
sibling expands to fill the space.

## Resizing

Drag the divider between two groups to change their split ratio. The
ratio is clamped so neither side drops below 10 %.

## Closing

- Close a tab with its `×` (or middle-click). If that empties a group,
  the group collapses.
- `Ctrl+Shift+W` closes the whole focused group (only when more than
  one group exists — you can never end up with zero groups). The
  dashboard is never closed; if it lived in the closed group it
  relocates to a surviving group.

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| `Ctrl+\` | Split the focused group horizontally (new group on the right). |
| `Ctrl+Alt+\` | Split the focused group vertically (new group on the bottom). |
| `Ctrl+Shift+W` | Close the focused group (when more than one exists). |
| `Ctrl+Tab` / `Ctrl+Shift+Tab` | Cycle focus to the next / previous group in tree order. |
| `Ctrl+1` … `Ctrl+9` | Focus the Nth group in tree order. |

These macro-level shortcuts yield to a focused **chat** tab, which binds
the same keys for its own internal [multi-chat groups](multi-chat.md).
To split a chat tab out into its own group, drag its tab to an edge.

## Persistence

Your tab-group layout is saved per browser (localStorage) and restored
on the next visit — including which groups exist, their split ratios,
and which tab is active in each. Layout is intentionally not part of the
URL; bookmark the route, not the arrangement.
