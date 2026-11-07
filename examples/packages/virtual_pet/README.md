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

The companion uses the host's `surface: window` overlay. The reviewed package defaults to a small transparent, always-on-top **Tauri** desktop window after it loads successfully. Web and `kt app` (pywebview) keep the in-application overlay; unsupported native pointer capabilities or a failed readiness handshake also retain that fallback.

- **Desktop choice:** Use **Settings → Overlays → Show on the desktop** to choose in-app display explicitly, retry a failed desktop launch, hide/show, or reset native placement. Reset does not change visibility or this choice. A hidden pet retains a device-local reset request, applied after native restoration and before its next presentation; it does not duplicate the native position store. The pet needs no additional desktop confirmation once enabled; other extensions still require their existing permission.
- **Background behavior:** Minimizing the main Tauri window, switching to another app, or closing to the tray leaves the pet available. Explicit app exit closes it. Native position is restored per backend/user, with disconnected-monitor and work-area reconciliation.
- **Focused work:** The desktop pet follows the most recently focused KT main/session-popout window, not every background session. Focusing the pet or another app retains that choice. Host/user changes and loss of the main-window relay invalidate it; switching presentation or reconnecting never replays historical finite cues.
- **Settings navigation:** The context menu's **Pet settings** item opens and raises the main window's Virtual pets section.

- **Movement:** Click and drag the pet to reposition it on your screen.
- **Controls:** Right-click the body, or focus it and press the menu key / `Shift+F10`, for settings and hide actions. Hover and ordinary focus stay quiet. Escape closes the menu without hiding the pet; complete management remains available in Settings, including on touch devices.
- **Placement & Visibility:** Handled by the host's standard overlay manager (**Settings → Overlays**), which persists your placement and visibility preferences locally.
- **Click-through:** Only the body, an open context menu and a visible failure-dismiss button intercept pointer events; the surrounding overlay area remains click-through.
- **Non-session pages:** When viewing non-session views (such as Settings or Catalog), the pet remains in its calm idle animation.

### Session Work Status

When viewing an active live session, the companion displays a small status bubble reflecting observed session state via [`useWorkContext()`](../../../docs/en/guides/ui-extensions.md#observed-session-work):
- **Processing:** Displays while observed creatures are processing or awaiting background jobs. In multi-creature sessions, the session remains busy until all observed work finishes.
- **Input required:** Displays when an interactive input prompt is awaiting user attention.
- **Response ready:** Displays for **3 seconds** upon observed work settlement, then disappears. This indicates observed work settlement rather than an attention-level completion counter or a guarantee that a final response turn has finished (for instance, a background job can complete before an auto-wake reporting turn begins).
- **Task failed:** Displays for **8 seconds** (or until manually dismissed). New session work clears past failure notices.

A fixed two-line feedback slot keeps the pet's scale, hit region and gaze origin unchanged when a message appears, changes or expires. The slot itself does not intercept input.

Switching sessions, refreshing, or mounting establishes a new baseline without replaying past completed or failed tasks. Changing locales or receiving periodic snapshots does not restart message timers, and time spent while the page is hidden counts toward the display deadline. An ARIA polite live region announces status messages for screen readers, while the sprite's accessible name reflects the pet's display name.

Authored controls and status messages support English, Simplified and Traditional Chinese, Japanese, German, and Korean via `useLocale()`, falling back to English. Imported pet names and backend diagnostic details remain in their source language.

## Animations and Interactions

The pet uses sprite rows defined in standard Codex pet sheets:
- **Idle (Row 0):** Columns 0–5 cycle across a relaxed **6.60-second** loop (holding 1680, 660, 660, 840, 840, and 1920 ms per frame). Used during normal idle, when waiting for input, and in the settings preview.
- **Task-Start Cue (Row 7 - Processing):** When an active session starts working, the pet plays an energetic animation (columns 0–5 holding 120, 120, 120, 120, 120, and 220 ms per frame) for **three cycles (2.46 seconds)** before returning to idle while work continues. Early task completion stops the cue immediately.

### Direct Gestures

Direct interactions take precedence over passive idle and task cues:
- **Hover (Jump):** Moving the mouse over the pet triggers up to **three jump cycles** (Row 4, **2.52 seconds** nominal). The shared hover/click/drag rectangle fits the first idle pose's visible pixels (alpha at least 32/255), not the whole atlas cell. It stays fixed across animation frames and scales with the artwork; unreadable or empty cells use the centered 60% of each dimension. This excludes transparent padding without changing the rendered sprite or gaze radius. Lingering over the pet does not loop the animation, and touch input does not synthesize hover.
- **Click / Key Press (Wave):** Clicking the pet or pressing `Enter` or `Space` plays up to **three wave cycles** (Row 3, **2.10 seconds** nominal). The in-app pet also waves upon its initial decoded appearance; switching pets in either surface waves once. Initial native presentation and reconnection do not replay a greeting. Repeated clicks do not queue or restart the wave. Pointer activation completes only on release inside the hit region; releasing outside cancels even below the drag threshold. Assistive-technology click activation is supported separately from the drag path.
- **Dragging (Directional Running):** Dragging the character triggers a directional run (Row 1 for right, Row 2 for left, up to **3 cycles** or **3.18 seconds** nominal per segment):
  - Movement of 6px starts a drag; 6px of horizontal displacement selects the initial direction.
  - Reversing direction requires a **6px** rollback from the furthest point reached in the current direction, immediately interrupting the old cue and starting the new direction at frame 0.
  - Continuing in the same direction or pausing does not restart the cue. Releasing the mouse stops running immediately without triggering a click wave. If the sprite sheet lacks directional rows, the pet remains idle during the drag.

### Priority and Accessibility

- **Gesture Priority:** `Drag > Wave > Jump > Task-start cue > Gaze > Idle`. A higher-priority action immediately replaces a lower-priority cue without queueing. Interrupted one-shot task-start cues are not re-armed when the interaction ends.
- **Visibility Changes:** Hiding the page cancels active gestures and cues, pausing animation scheduling. If a new live task starts while the page is hidden, returning before its deadline displays only the remaining portion of the cue window.
- **Reduced Motion:** If system reduced motion is enabled, animations stay on a static idle frame while drag gestures remain fully interactive.
- **Explicit management:** Right-click the body for **Pet settings** and **Hide pet**. Hover and ordinary focus never reveal management buttons. A focused body also accepts the menu key or `Shift+F10`; use arrow keys / Home / End to select, Enter / Space to activate, and Escape to close and return focus. Tab exits without trapping focus. Outside clicks, window blur, hidden pages and identity changes close the menu without a backdrop or a desktop-wide input target. The menu is clamped inside the existing window and does not reflow the pet. The full settings page remains the discoverable/touch fallback; no additional long-press gesture is introduced. Task-failure messages retain their own dismiss button.

Animation sequences and relative frame rates follow OpenAI's public [hatch-pet reference](https://github.com/openai/skills/blob/c25113bf4c64c8dba6bfe61acf06051d79aa43f6/skills/.curated/hatch-pet/references/animation-rows.md), with idle pacing tuned for a calm desktop presence. Direct gestures and task-start cues play up to three cycles before settling.

### V2 mouse proximity gaze

Complete V2 sheets enable 16 held direction poses from rows 9–10, starting upward and proceeding clockwise in 22.5° steps. These are poses, not a looping animation: up is `(9, 0)`, right `(9, 4)`, down `(10, 0)`, left `(10, 4)`, following the [Codex Pet Lab V2 format](https://codexpetlab.com/docs/v2-spritesheet).

The entry radius is one rendered long-edge size with an 80 ms dwell; exit uses 1.4 times that size and 120 ms. Angle hysteresis and a center dead zone suppress flicker. Gaze does not change work bubbles or restart their deadlines. The body's tight hit rectangle is excluded from proximity gaze: while the mouse remains over the body, completed jump/wave/drag cues settle to Idle. Leaving the body starts a fresh 80 ms gaze dwell; transparent cell padding outside the body can still participate in proximity sensing. This uses current rendered hit geometry rather than only enter/leave events. Dragging, controls, settings previews, pause and reduced motion suppress gaze. Turn it off in **Settings → Virtual pets → Look toward the mouse**; the device-local preference is scoped by backend/user. A failed settings write preserves the previous value in both the checkbox and companion and reports the failure. V1 and incomplete V2 direction rows keep the previous behavior, without blank poses or re-importing. Legacy resources retain their keys and selections.

Desktop samples use physical screen position converted to the overlay's CSS viewport, including zoom/DPI; native drag displacement is measured from the press in screen space. The proximity radius is never an input-blocking rectangle. Wayland lacks the required global pointer capability and falls back to in-app display. Windows checks at 100% display scaling covered all 16 directions with real V2 artwork, tight padding/body hit regions, minimize/tray behavior, drag/release, and a secondary display with negative coordinates. A further 75-case native WebView check covered application zoom at 60%, 100%, 125%, 150% and 200%, all three reading sizes, and five work states: the pet, controls and hit rectangle remained within the window, and work-state changes did not move the target. This validates application zoom, not mixed-DPI transitions. After replacing the toolbar, 90 native-WebView combinations of those five zoom levels, three reading sizes and six authored languages checked menu bounds, stable body geometry, scroll-to-focused items and unchanged ancestor scroll offsets. CDP-generated right-click and keyboard input also checked menu opening/dismissal, settings navigation and hide/show; these checks did not move the OS cursor.

A 365-second Windows minimization check with the debugger detached retained the visible pet, native pointer delivery and synthetic single-session updates after five minutes; restoring the main window cleared the test context without leftover feedback. That WebView continued to report `document.hidden === false` while Win32 reported the main window minimized. This is evidence for actual minimization, not intensive hidden-page timer throttling. Mixed-DPI transitions, suspended/strongly throttled renderers and other platforms still require validation on the target machine.

## Interaction contract

These are product acceptance rules, not a claim of full accessibility conformance:

| Contract | Acceptance rule |
|---|---|
| Predictable spatial target | Transparent atlas padding is not an invisible hover/click/drag target. A stable idle-pose rectangle approximates the body; it is not per-frame or pixel-perfect hit testing. |
| Separate sensing from activation | Proximity may select a gaze pose, but must not reveal management controls, capture input or initiate dragging. |
| Stable target under feedback | Animation and session-status updates do not move the target or resize the pet. Explicit placement/viewport/text-size changes may resize it. |
| Explicit, reachable management | Only a body context-menu request opens settings/hide shortcuts. Menu and keyboard activation are equivalent; Escape, outside interaction and focus departure dismiss it. Menu visibility never resizes the pet or turns transparent padding into a backdrop. |
| Cancellable, equivalent activation | Release outside cancels a click; drag release never becomes a wave. Keyboard and assistive activation remain usable, without stealing desktop focus on proximity. |
| User authority and honest feedback | Hide and in-app choices persist. Reconnection does not replay old cues; a bubble describes only the latest focused session, not all sessions or task completion guarantees. |

The hover/focus and cancellation checks draw on W3C's [Content on Hover or Focus](https://www.w3.org/WAI/WCAG22/Understanding/content-on-hover-or-focus.html) and [Pointer Cancellation](https://www.w3.org/WAI/WCAG22/Understanding/pointer-cancellation.html). Larger hit targets can aid accessibility, but an invisible target must not steal unrelated desktop input. Future reviews should test transitions and empty space, not just events dispatched directly onto a component.

## Desktop host implementation notes

The host reuses `overlay-*` windows, not an engine-level pet service. The native content fills its zoom-adjusted parent rather than multiplying viewport units by the application's CSS zoom. The viewport companion reports `ready(false)` while empty or decoding, then `ready()` after decoding. Only then does the host create the hidden native window and start its readiness timeout. An empty library therefore does not consume that timeout or require a manual retry after the first import. If content becomes unavailable during `opening`, the host cancels the attempt and clears its timer; a new attempt waits for the old window to finish closing. This cancellation does not apply during `presenting`, when retiring the viewport copy is intentional, or clear a genuine failed attempt. The native renderer acknowledges its creation generation after its sprite loads; the main renderer then retires its viewport copy and presents the native window. Missing capabilities, timeout or unexpected close retain the in-app fallback. Native labels include the backend/user scope; the window-state plugin restores position without restoring overlay visibility ahead of the readiness handshake.

The default-desktop exception is an exact **UI/manifest content allowlist**, not a claim that a package name proves its origin. `api/ext/policy.py:desktop_pet` hashes `kohaku.yaml` and the sorted files below `ui/`, including relative paths and NUL separators, with CRLF normalized to LF. A UI change requires review and a new `DESKTOP_PET_DIGEST` plus policy tests. Modified copies and development-URL overrides retain ordinary extension permission rules. No manifest/SDK `firstParty` flag grants the exception. Backend handler execution remains governed by the existing package system, not this UI hash.

`ext/desktopContext.js` relays memory-only, sequence-checked snapshots from the latest focused main/session-popout renderer. It does not query all sessions, persist work snapshots, acknowledge tasks, or relay auth/launch tokens. Receipt and heartbeat paths invalidate stale producers and establish reconnect baselines. Runtime identity changes rotate the main relay's epoch; queued producers must match that authority/epoch, including token-only sessions. Credentials remain local and are never part of the messages. A session popout adopts the main identity only during bootstrap. A later host/user/runtime-authority change closes its relay, unmounts its session and releases scoped transports before requesting native closure; it never redirects the old session to the new backend. A heartbeat gap alone does not close a popout. Overlay renderers continue following the main identity. `useWorkContext()` remains the public work-context API.

Native input uses the original press plus a per-press identity; coalesced IPC cannot reuse another gesture's origin. Captures expire without renewal and are released on cancel, blur, removal and teardown. Hidden controls do not enter native hit regions. Browser pointer handling remains viewport-local and mouse-only.

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
- **Isolation:** Mutations serialize per user library. Legacy library entries derive interaction and gaze capabilities dynamically on load without requiring re-imports or schema migrations. Libraries are scoped to the local backend process and are not synchronized across distributed Lab nodes.
