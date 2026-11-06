# KohakuTerrarium Live2D companion

A UI extension for a dragon and DeepSeek-chan companion. Its stable package ID
is `live2d-pet-demo`. It includes adapters and the Cubism runtime. Dragon and
DeepSeek resources are private user imports; no model artwork is bundled.

## Use

Open **Settings → Overlays → Live2D 宠物**, expand its settings and select
**显示宠物**. The separate Live2D settings section and tab use the same state.
Settings group appearance, interaction behavior, control mode and advanced options;
the action-set controls have their own section. Narrow panels stack controls and
mode descriptions vertically.

- Choose either model, default appearance (including the dragon's juvenile
  form), idle animation and **50–300%** size. Sizes fit the available viewport.
- **High quality (4K)** is the default for new preferences. **Performance (2K)**
  halves each texture dimension. Existing saved quality choices are preserved.
- Each model remembers its own size, appearance and idle. Quality, gaze,
  physics, work-following, talking mouth, auto-sleep and Debug are shared preferences.
- Normal mode shows only the model. Drag the model to move it; tap its head or
  body to interact; right-click for model, quality, size, idle and appearance.
  **关闭宠物** stays in a separate footer while the menu content scrolls.
  It hides the pet and releases control; show it again
  from Live2D settings. The size slider previews its percentage while dragging
  and applies the size on release, keeping the menu stationary during the drag.
  Menus stay inside the visible host area; desktop menus use the monitor work
  area, excluding system bars, even when the transparent window extends offscreen.
- In the Tauri desktop app, enable **Show on the desktop** under Overlays to
  move the companion outside KT. Browser and pywebview hosts keep the in-app
  overlay. **Reset position** recovers a misplaced desktop companion.
- **交互模式** selects **普通 Pet** (default), **可选主动表达**, or
  **专用 L2D Agent**. Existing enabled-control preferences migrate to optional
  expression; other preferences keep the automatic Pet default.
- Debug is off by default. It adds a compact drag/settings/close bar, work badge,
  action menu, hints and frame diagnostics. The preview tab additionally offers
  zoom, pan, pause, backgrounds, quality comparison and work-action rehearsals.
  Turning Debug off cancels rehearsals and restores the latest real work state.
- **说话口型** is on by default and can be turned off in settings or the right-click
  menu. Both models and all three interaction modes follow fresh reply text with
  varied mouth openings and punctuation pauses. Completion, interruption, tool
  execution and connection loss release the mouth; manual sleep, pause and hidden
  windows suppress it. Switching conversations/models never replays old text.
  This is silent text animation, not voice or phoneme alignment, and adds no LLM
  calls. It is independent of **跟随会话动作** and preserves expression/appearance.
- At host startup, if no overlay in the `pet` startup group is visible, KT opens
  this extension and selects the dragon. Existing visible pets take precedence.
  Closing all pets does not reopen one during that run; the next startup checks
  again. Disabled/uninstalled extensions are never candidates.

Settings use package-scoped `kt.preferences`: immediate local state and KT's
existing debounced backend UI-preference store. They survive the source app's
private browser profile. Grouped pet visibility is also persisted per host/user.
When persistence is unavailable, controls continue with local/in-memory state.

## Architecture and resource budgets

One extension-owned runtime serves settings, preview and companion; mounting or
closing a settings component does not start or stop the controller. Its disposal
aborts pending action reads, releases subscriptions and invalidates late callbacks.
UI ownership and temporary creature-action bindings have separate leases. The
backend grants only one controller; renderer health is reported by the actual
model frame before an action binding can become connected.

One settings store serves the settings panel, preview and companion. A host/user
scoped BroadcastChannel forwards settings, observed work and transient actions
between the main window and desktop companion. The main window owns the
controller; the secondary window only renders. Only one model renderer is
mounted for a preview/overlay pair. Model or quality changes
navigate with a fresh signed asset URL and release the old WebGL context;
appearance, size and switches update the current renderer without reloading.
Messages validate the frame source, origin and navigation channel.

Both qualities share the same moc3, motions, expressions, physics and UV layout.
Texture variants use lossless WebP prepared offline; there is no per-frame resampling, GPU
readback or zoom-triggered texture loading.

| Model | High quality | Performance | Texture pixels: high / performance |
| --- | --- | --- | --- |
| Dragon | 3 × 4096² | 3 × 2048² | 192 / 48 MiB |
| DeepSeek | Largest edge 4096 | Largest edge 2048 | 148 / 37 MiB |

These are RGBA texture pixel budgets, not process memory or total GPU memory.
DeepSeek also uses two 1024² mask targets (8 MiB of RGBA pixels); model, canvas,
decoding and driver allocations are additional. See [performance results](PERFORMANCE.md).

Companions target 30 FPS in both qualities. Hidden documents stop model updates
and drawing. Debug diagnostics retain at most ten seconds of samples and report
CPU update/draw-submission cost; they do not measure asynchronous GPU execution.

The frame fits the model envelope inside its recommended width and height,
plus 80 CSS pixels of chrome only in Debug (minimum control width 200 px).
Debug labels follow visible mesh bounds in 8 px steps, using the existing
geometry pass rather than reading texture pixels. The host clips input to model/control rectangles; empty
space outside them passes clicks and scrolling through. CPU mesh checks normally
run at most five times per second, rising during pose transitions and wing beats
to preserve moving edges. The current visible mesh rectangle replaces the old
maximum-wing envelope; it expands immediately and holds shrinking edges for up
to 200 ms. DOM-only changes reuse cached geometry, and unchanged work badges
are not rewritten for speech chunks. Space inside the rectangle, including between wings, still receives
input. The same rectangles drive iframe clipping and native window hit testing.
Transparent canvas margins may extend past the app edge; the desktop window can
leave KT entirely. This is rectangular input cropping, not per-pixel clipping.
Native resizing preserves the window's bottom center using native coordinates;
model changes do not push the character sideways by the change in canvas width.
Use Settings → Overlays → Reset position to bring a misplaced window back.
Placement recovery uses native monitor coordinates (physical pixels on Windows,
desktop points on macOS and logical coordinates on Linux/GTK), never Chromium screen origins.
Legacy unversioned placements and Linux's old physical-coordinate placements are ignored.
The shell owns persistence and confirms geometry through native events. A reset requested
while the window is closed is consumed on its next open; later manual moves are retained.
Native overlays require `overlayWindows`, `overlayResize` and
`overlayPlacementNative`; older shells use the in-app overlay.

## Work and interaction

Settings describe all three modes together. Dedicated mode's **Create / open
dragon chat** button finds loaded companions by their configured tool, including
inherited configurations. One match opens directly; multiple matches offer a
choice. A saved companion is reopened by default; **Change conversation**
selects a different one. No match opens the normal
KT new-session dialog with the companion configuration, a readable name, and
chat opening preselected. Creation still requires submitting that dialog.
The entry also shows the pet. Conversation models remain managed by KT.

The connection label appears only after binding succeeds. Pending, rejected,
hidden-renderer and released bindings show no connected conversation. Changing
the mode alone does not start an agent; dedicated conversation and action
control belong to the same agent, with no additional background agent.

Ordinary Pet and optional expression follow the conversation **selected by the
user**. Settings/files and switching to other apps retain the last selection;
background events never select a conversation. Session work state is aggregated,
while speech and action tools belong only to the selected creature. A channel
view has no creature speaker or action permission. The settings and pet menu show
**Following** or **Pinned** and offer **Open this conversation**.

Desktop chat popouts report an explicit focused selection to the main window;
the pet/menu does not change it. A browser has one controller page per local
engine. Other pages show **Take over on this page**; focusing a tab or a heartbeat
cannot steal control. Dedicated mode pins the explicitly chosen companion,
independently of the currently viewed chat.
Initial snapshots, reconnects and historical completions do not replay gestures.
Automatic work following does not create extra LLM requests. Optional agent
control uses one bounded long-poll connection and a five-second binding
heartbeat; it does not start another agent or drive per-frame model inference.

| State | Dragon | DeepSeek |
| --- | --- | --- |
| Idle | Gentle, authored wings or sleep | Authored natural idle |
| Running | Wing beats and body sway | Authored raised-index pose |
| Waiting for input | Open fan held at the side | Forward lean |
| Input resolved | Close fan, then lower hand | Blend back from lean |
| Ready | Wing stretch | Authored raise/hold/lower sequence |
| Failed | Concerned pose | Concerned pose |

Consecutive questions keep the waiting pose. Fast completion finishes the entry
and exit transition before playing the completion gesture. Work poses are
transient and restore chosen appearance. Manual sleep/pause take precedence.
The dragon can auto-sleep after 60 idle seconds; active work prevents it. Sleep
preserves the selected adult or juvenile form and restores hand poses on waking.
DeepSeek has no sleep asset. A completion cue reports KT's settlement event,
not independent verification that a task succeeded.

Appearance states are separate from actions: the dragon's adult/juvenile form and
DeepSeek's body type, clothing and accessories persist until the user changes them.
Settings and the context menu group them under **形象状态**. Sleep, speech, agent
actions and resetting poses/expressions preserve these choices in all three modes.

### Three layers

| Layer | Behavior | LLM cost |
| --- | --- | --- |
| Ordinary Pet | Existing session events select automatic actions; touch, gaze and idle remain local. | No injected action tools, control polling or additional inference. |
| Optional expression | A visible pet temporarily lends the selected creature an action tool and an on-demand skill. | Uses that creature's existing model; tool selection, context and continuation may add cost. |
| Dedicated L2D Agent | The packaged `companion` creature has its own role and explicitly configured action tool. | Its conversation uses the selected LLM; no automatic background agent or per-frame inference. |

Choose **专用 L2D Agent**, then **创建 / 打开龙娘对话** to select a running
companion or open KT's normal creation dialog using
`@live2d-pet-demo/creatures/companion` (display name `live2d_companion`).
The mode rejects ordinary creatures instead of silently converting them.
Switching pages or closing its chat tab preserves the pin; stopping or removing
the creature invalidates it and offers a replacement. Changing modes never
starts an LLM turn. The example also loads through
`kt run @live2d-pet-demo/creatures/companion`, but visual control needs a visible UI
attached to the same local engine. A separate CLI process cannot control an
unrelated desktop engine.

All three layers share an action catalog and player. `PetBehavior` owns observed
work transitions; the action selector translates states or explicit requests
into playback. The existing dragon/fish work routines remain model adapters.
`ActionPose` adds temporary parameter poses, expressions and sampled motions
without changing saved appearance or marking a task complete. Idle, manual
sleep and touch are local interactions, not LLM tasks.

### Registering an action set

In settings, export the current action set, edit the JSON and import it again.
The selected model remembers its catalog through the same host/user preferences
as appearance settings. **恢复内置动作集** restores the supplied catalog. Each
file contains `version: 1`, `model`, `label`, `defaults` and `actions`.
`defaults` maps `running`, `waiting`, `ready` and `failed` to action IDs. Those
IDs and any extra actions are also available to the agent.

An action has a description, default `seconds`, `maxSeconds` (1–15 seconds)
and one `playback` descriptor:

```json
{
  "description": "Two gentle acknowledging nods, then return upright",
  "seconds": 2,
  "maxSeconds": 4,
  "playback": { "type": "motion", "name": "Nod" }
}
```

Playback types:

- `preset`: `name` is `running`, `waiting`, `ready` or `failed`; preserves the
  existing model-specific authored transitions.
- `pose`: `parameters` maps existing model parameter IDs to finite values. The
  player blends in/out and clamps values to the model's actual ranges.
- `expression`: `name` selects an already loaded expression, such as DeepSeek's
  `脸红`; the expression is temporary and does not change saved appearance.
- `motion`: `name` selects an already loaded animation group or work clip, such
  as `Nod`, `Idle`, `FingerIn` or `RaiseIn`. It samples once and holds the last pose
  until release. It does not load arbitrary paths or network resources.

`Nod` is a local 1.55-second head-and-body gesture with two dips and a return
upright, calibrated for each model. It leaves mouth parameters to speech and
expressions. The dragon adapter also drives the rendered head axes after
physics. A static `pose` holds its angle; use `Nod` for visible nodding.

Work states can hold their mapped pose while the state remains active; explicit
agent gestures have bounded durations. Completion cues use the mapped action's
default duration. Unsupported model capabilities produce a visible settings
error, restore automatic built-in playback and prevent agent binding until the
catalog is fixed. Model loading/import itself remains separate from action-set
registration; this package ships adapters for the dragon and DeepSeek models.

### Agent binding and ownership

`l2d` lists semantic IDs and durations, performs one bounded action, or releases
it. Raw playback details stay out of the tool inventory. The expression skill
loads through KT's standard skill mechanism when needed; its body is not
injected into every prompt. The UI does not select an LLM provider or preset.

Authenticated routes bind one selected local creature to one visible renderer.
Only one main page may own the control lease at a time. Other pages receive a
conflict status; neither an initial background request nor a renewal steals an
active lease. Explicit takeover changes the controller generation and revokes
the preceding action binding. Ordinary Pet also participates in UI ownership,
but does not inject tools, poll actions or start an LLM call.
Renewals cannot recreate an expired lease. The renderer sends a real health
heartbeat; failure or ten seconds without a heartbeat clears readiness and
provides Retry. One package runtime outlives settings/preview components. Closing the pet drops
its renderer and action binding; disabling/reloading the extension, changing
hosts/users or exiting disposes its subscriptions, watchers, timers and channels.
Tool/skill updates retain the engine-managed graph context.

A 20-second lease expires after the UI disappears. Switching models, catalogs,
conversations or modes revokes old delivery; command IDs and deadlines reject
replays. A new lease has a fresh queue so an old long poll cannot consume its
commands. Only the latest pending action is retained.

Temporary tools are removed on disconnect. Explicitly configured companion
tools remain registered but reject playback without a lease. Existing skill
objects and enabled states are restored; temporary UI ownership does not write
persistent skill toggles. Instructions already read can remain in chat history.
Real waiting-for-input, manual sleep/pause and touch take precedence. Dispatch
is not proof of visible playback. Arbitrary agent-supplied parameter streams,
unbounded agent poses and remote-creature control are not exposed.

## Install and import resources

```sh
kt install ./examples/packages/live2d_pet -e --no-deps
```

Both models require a user import before rendering.

In **Settings → Extensions → Live2D 宠物**:

1. Check **已适配模型** (adapted models). Every registered adapter appears,
   including models whose resources have not been imported. The status shows
   **已导入** (imported) or **未导入资源** (not imported).
2. Choose **导入 L2D 模型** and select a ZIP containing the model's runtime
   export, preserving its folder structure. KT reads the adapter binding from
   the model file; no manual adapter selection is needed. Original dragon and DeepSeek
   exports are also recognized automatically.
3. After a successful import, KT selects the model and offers both texture
   quality levels. Switch models using the regular character selector.

An **adapter** defines a model's expressions, gestures, mouth and interaction
mapping. **Resources** are its model, textures and animation files. The adapter
list describes supported models; it is not a list of files installed on your
computer. Importing resources does not create an adapter. For another model,
first [add and register its adapter and model-file binding](ADAPTERS.md), then
import its matching ZIP. Unsupported or mismatched resources are rejected.

For Dragon, include `kohaku.model3.json`, `kohaku.moc3`, referenced textures and
physics, `idle.motion3.json`, `sleep2.motion3.json`, and the five supplied
expression files. For DeepSeek, include `.model3.json`, `.moc3`, textures, physics, `Animation/`
and `Exp3/`; omit PSD and other authoring files. A prepared export with
`high.model3.json` is also supported. Limits are 128 MiB uploaded, 512 MiB
unpacked and 512 entries. Missing resources show an import hint; a failed
validation/conversion leaves the previously imported version selected.

Resources are scoped to the current KT host and user. A failed replacement leaves
installed resources unchanged. Imported model files and account credentials are
not added to the repository. Runtime and import responsibilities are described in
[Adding a model adapter](ADAPTERS.md).

Model textures, MOC3 binaries, motions and expression files are excluded from the
package and its Git history. Importing creates per-user high/standard variants;
model files must never be written below the extension's public `ui/` directory.

## Rebuild runtime and prepare private resources (development only)

Install frontend dependencies and libwebp's `cwebp` encoder, obtain the official
Cubism SDK for Web 5-r.5, and provide the model folders. On macOS, from the repository root:

```sh
npm ci --prefix src/kohakuterrarium-frontend
node examples/packages/live2d_pet/scripts/prepare.mjs /path/to/kohaku /path/to/CubismSdkForWeb-5-r.5 /private/output/dragon
node examples/packages/live2d_pet/scripts/build.mjs
kt install ./examples/packages/live2d_pet -e --no-deps
```

The scripts copy runtime resources and generate both texture sets using the
existing `sips` resizing followed by `cwebp -lossless -exact -metadata icc`.
Set `CWEBP` to the executable path when it is unavailable on `PATH`.
WebP preserves the resized RGBA pixels, including RGB beneath fully transparent
pixels, and retains the resized PNG's embedded ICC profile (`sips` may normalize
standard sRGB profiles). Non-sRGB PNG gamma/chromaticity data without an ICC
profile, 16-bit PNGs and animated PNGs are rejected. Encoding reduces disk
and download size; texture dimensions and RGBA memory budgets remain the same.
The scripts validate paths, lossless format and dimensions, preserve originals
and do not import PSDs. After publishing each new quality manifest, they remove
only corresponding generated `high/texture_N.png` and `standard/texture_N.png`
files for the current input indices.
Only the generic `ui/runtime/` SDK/player is tracked. Model output must be outside
the extension package. ZIP the prepared model directory and import it through
settings; normal users need neither the preparation scripts nor `cwebp`.
The DeepSeek preparation command is
`node examples/packages/live2d_pet/scripts/prepare-deepseek.mjs MODEL_FOLDER PRIVATE_OUTPUT_FOLDER`.
Standalone static previews cannot access private imports; use KT's authenticated
preview for either model.

DeepSeek art/separation: 神宫凉子. Live2D: 霓虹猫_NeonNeko. The supplied usage
notice prohibits unauthorized redistribution of the complete model. Do not publish DeepSeek art with this extension. Its adaptation code and credits
remain included. SDK license notices are copied into `ui/runtime/`; Core and
Framework retain their licenses. The SDK retains its upstream provenance and license. Users supply model resources
under their own applicable permissions; importing does not grant redistribution rights.

## Validation

```sh
node --test examples/packages/live2d_pet/scripts/prepare*.test.mjs
node --test examples/packages/live2d_pet/scripts/textures.test.mjs
npm test --prefix src/kohakuterrarium-frontend -- --run live2dPet overlayStartup
```

Real-model tests opt in with `KT_LIVE2D_DRAGON_DIR` and
`KT_LIVE2D_DEEPSEEK_DIR`, each pointing to a private prepared/imported version
directory. Without those resources, real-model cases skip; synthetic import and
user-isolation tests still run. Verify normal/Debug rendering, gestures, quality changes and restart
restoration in the native KT WebView as well as running automated checks.
