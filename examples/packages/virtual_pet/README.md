# Virtual pets

An installable KohakuTerrarium extension that adds an animated desktop companion to your dashboard. The pet lives in a lightweight application overlay, reacts to direct interactions, and quietly reflects the status of whatever live session you are viewing.

Key features:
- **Session-aware overlay:** Follows the currently active session via `useWorkContext()`, remaining idle on non-session views like Settings. No creature configuration changes, extra LLM calls, or bundled artwork are required.
- **Customizable library:** Import standard Codex V1/V2 sprite sheets through application settings.
- **Interactive animations:** Supports hover jumps, click greetings, and directional drag movements alongside task-start feedback.

## Quickstart

### Prerequisites

The extension requires a KohakuTerrarium backend and dashboard that include `useWorkContext`, `ActionButton`, `SettingsGroup`, and `SettingsRow` in their public UI SDK exports. Note that installing this package does not upgrade an older host dashboard.

### Installation

From your repository root, run the following command in the environment serving your active backend (if using a remote backend, run this command on that backend):

```sh
kt install ./examples/packages/virtual_pet -e --no-deps
```

After installation, reload your dashboard.

- **Single-user hosts:** The extension's UI is enabled automatically.
- **Multi-user hosts:** An administrator must enable the package UI under **Catalog → Installed** before it appears. For details on extension permissions, see [UI extension loading and security](../../../docs/en/guides/ui-extensions.md#loading-assets-and-security).

### Choosing and Managing Pets

1. Open **Settings → Virtual pets** in the dashboard.
2. Click **Import a pet** and select a ZIP archive containing `pet.json` and its PNG or WebP sprite sheet (optionally enclosed in a single top-level folder).
3. The new pet is validated, imported, and selected automatically. You can preview pets, switch selections, or remove pets at any time. Removing a selected pet automatically switches to the first remaining pet.

Pet libraries and active selections are scoped to the authenticated user on the active backend. They are stored locally on that backend and are not shared across different users, separate backend processes, or distributed Lab nodes. Cross-process writes to a shared configuration directory are uncoordinated.

## Companion Overlay

The companion floats in an in-application overlay (`surface: app`), staying visible across tabs as you navigate the dashboard:
- **Movement:** Click and drag the pet to reposition it on your screen.
- **Controls:** Hovering or focusing on the pet reveals settings and hide buttons (on touch devices, controls remain visible).
- **Placement & Visibility:** Handled by the host's standard overlay manager (**Settings → Overlays**), which persists your placement and visibility preferences locally.
- **Click-through:** Only the pet and its control buttons intercept pointer events; the surrounding overlay area remains click-through.
- **Non-session pages:** When viewing non-session views (such as Settings or Catalog), the pet remains in its calm idle animation.

### Session Work Status

When viewing an active live session, the companion displays a small status bubble reflecting observed session state via [`useWorkContext()`](../../../docs/en/guides/ui-extensions.md#observed-session-work):
- **Processing:** Displays while observed creatures are processing or awaiting background jobs. In multi-creature sessions, the session remains busy until all observed work finishes.
- **Input required:** Displays when an interactive input prompt is awaiting user attention.
- **Response ready:** Displays for **3 seconds** upon observed work settlement, then disappears. This indicates observed work settlement rather than an attention-level completion counter or a guarantee that a final response turn has finished (for instance, a background job can complete before an auto-wake reporting turn begins).
- **Task failed:** Displays for **8 seconds** (or until manually dismissed). New session work clears past failure notices.

Switching sessions, refreshing, or mounting establishes a new baseline without replaying past completed or failed tasks. Changing locales or receiving periodic snapshots does not restart message timers, and time spent while the page is hidden counts toward the display deadline. An ARIA polite live region announces status messages for screen readers, while the sprite's accessible name reflects the pet's display name.

Authored controls and status messages support English, Simplified and Traditional Chinese, Japanese, German, and Korean via `useLocale()`, falling back to English. Imported pet names and backend diagnostic details remain in their source language.

## Animations and Interactions

The pet uses sprite rows defined in standard Codex pet sheets:
- **Idle (Row 0):** Columns 0–5 cycle across a relaxed **6.60-second** loop (holding 1680, 660, 660, 840, 840, and 1920 ms per frame). Used during normal idle, when waiting for input, and in the settings preview.
- **Task-Start Cue (Row 7 - Processing):** When an active session starts working, the pet plays an energetic animation (columns 0–5 holding 120, 120, 120, 120, 120, and 220 ms per frame) for **three cycles (2.46 seconds)** before returning to idle while work continues. Early task completion stops the cue immediately.

### Direct Gestures

Direct interactions take precedence over passive idle and task cues:
- **Hover (Jump):** Moving the mouse over the pet triggers up to **three jump cycles** (Row 4, **2.52 seconds** nominal). Lingering over the pet does not loop the animation, and touch input does not synthesize hover.
- **Click / Key Press (Wave):** Clicking the pet or pressing `Enter` or `Space` plays up to **three wave cycles** (Row 3, **2.10 seconds** nominal). The pet also waves once upon its initial decoded appearance or when switching pets. Repeated clicks do not queue or restart the wave.
- **Dragging (Directional Running):** Dragging the character triggers a directional run (Row 1 for right, Row 2 for left, up to **3 cycles** or **3.18 seconds** nominal per segment):
  - Movement of 6px starts a drag; 6px of horizontal displacement selects the initial direction.
  - Reversing direction requires a **6px** rollback from the furthest point reached in the current direction, immediately interrupting the old cue and starting the new direction at frame 0.
  - Continuing in the same direction or pausing does not restart the cue. Releasing the mouse stops running immediately without triggering a click wave. If the sprite sheet lacks directional rows, the pet remains idle during the drag.

### Priority and Accessibility

- **Gesture Priority:** `Drag > Wave > Jump > Task-start cue > Idle`. A higher-priority action immediately replaces a lower-priority cue without queueing.
- **Visibility Changes:** Hiding the page cancels active gestures and cues, pausing animation scheduling. If a new live task starts while the page is hidden, returning before its deadline displays only the remaining portion of the cue window.
- **Reduced Motion:** If system reduced motion is enabled, animations stay on a static idle frame while drag gestures remain fully interactive.

Animation sequences and relative frame rates follow OpenAI's public [hatch-pet reference](https://github.com/openai/skills/blob/c25113bf4c64c8dba6bfe61acf06051d79aa43f6/skills/.curated/hatch-pet/references/animation-rows.md), with idle pacing tuned for a calm desktop presence. Direct gestures and task-start cues play up to three cycles before settling.

## Supported Imports and Limits

Pet archives must contain a `pet.json` manifest and a relative `spritesheetPath` pointing to a still, transparent PNG or WebP image (animated images are not supported). Manifests require `id` and `displayName`; `description` is optional and `spriteVersionNumber` defaults to 1.

| Version | Dimensions | Grid | Cell Size |
|---|---|---|---|
| Codex V1 | 1536 × 1872 | 8 columns × 9 rows | 192 × 208 px |
| Codex V2 | 1536 × 2288 | 8 columns × 11 rows | 192 × 208 px |

- **Required rows:** Row 0 (Idle) and Row 7 (Processing) must contain a non-empty contiguous prefix of frames starting at column 0 (detected with alpha ≥ 16).
- **Optional rows:** Row 1 (Run Right), Row 2 (Run Left), Row 3 (Wave), and Row 4 (Jump) are enabled when they contain a non-empty contiguous prefix of frames starting at column 0 within their defined column counts (8, 8, 4, and 5 columns respectively; shorter contiguous prefixes work with their own summed duration). Missing or gapped rows disable that specific interaction, falling back to idle.
- **Archive limits:**
  - Maximum archive size: **8 MiB ZIP**
  - Maximum expanded content: **16 MiB**
  - Maximum archive entries: **32**
  - Maximum manifest size: **16 KiB**
  - Archives must contain exactly one `pet.json`. Absolute paths, parent directory traversals (`..`), symbolic links, and encrypted members are rejected.

## Architecture and Storage

A single package-owned backend handler coordinates between Settings and the companion overlay via KohakuTerrarium's standard owner RPC (`import`, `select`, `remove`, `sprite`, and `thumbnail`):
- **Storage Locations:**
  - Without a signed-in user: `<KT config dir>/virtual-pet/`
  - Authenticated user: `<KT config dir>/users/<user id>/virtual-pet/`
- **Data Layout:** Pet metadata and the active selection are stored in an atomically updated `index.json`, while sprite sheets are stored separately as content-addressed files. Content hashes prevent duplicate imports.
- **Isolation:** Mutations serialize per user library. Legacy library entries derive interaction capabilities dynamically on load without requiring re-imports or schema migrations. Libraries are scoped to the local backend process and are not synchronized across distributed Lab nodes.
