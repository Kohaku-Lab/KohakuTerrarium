# Adding a model adapter

Model resources and model behavior are separate. Importing a ZIP does not infer
its expressions or parameter semantics. The package currently registers the
dragon and DeepSeek adapters. Both models require private imports. Arbitrary
ZIPs are not treated as DeepSeek, and a ZIP never installs executable adapter code.

## Registration checklist

1. Define the runtime adapter in `ui/models/<id>.js` and register it in
   `ui/models/index.js`.
2. Register its supported semantic actions in `ui/action-sets.json`.
3. For imported models, add a validator/normalizer to
   `kt_live2d/model_adapters.py` and register the same ID in `IMPORT_ADAPTERS`.
4. Rebuild the runtime and validate the real model in both quality levels and
   all three interaction modes.

Settings lists all runtime adapters under **已适配模型**, independently of
whether resources are installed. Users choose **导入 L2D 模型** and select a ZIP;
its model file determines the adapter. There is no adapter selector. Keep the
JavaScript, action-set and Python registry IDs consistent; adding an adapter does
not ship its artwork. Both shipped adapters appear even before their resources
are imported.

## Runtime adapter

Add `ui/models/<id>.js` and register it in `ui/models/index.js`. Use the existing
dragon and DeepSeek adapters as worked examples. Each adapter supplies:

| Field | Purpose |
| --- | --- |
| `id`, `name`, `size`, `fps`, `bundled` | Stable identity, display and resource policy |
| `idleChoices`, `interaction` | Idle choices, click cycle and optional sleep |
| `expressions`, `expressionGroups` | Appearance labels and mutually exclusive groups |
| `appearanceStates` | Persistent body/age/outfit choices backed by expression resources |
| `normalizeExpressions`, `toggleExtras` | Model-specific appearance dependencies |
| `aliases`, `mouth`, `headParts` | Rendered head axes, mouth parameter and touch areas |
| `boundsParameter` | Optional parameter whose extremes define framing bounds |
| `nod` | Head/body keyframes for the shared nod animation |
| `workMotions`, `workPose`, `exitParameters`, `releaseDuration` | Authored clips and state transitions |
| `configurePreview`, `framingPan` | Model-specific preview labels and framing |
| `shortName` | Short display name for import feedback |

The player calls `workPose({state, t, action, previous, exitFrom, idleStyle,
model, sample})`. Return a map of parameter IDs to target values. `sample(name,
seconds)` samples a loaded clip without leaving parameter changes behind.
The shared runtime handles timing, blending, clamping, physics, speech layering,
cleanup and action ownership. Keep model IDs and model-specific parameter names
out of that shared pipeline. `DragonModel`/`DragonPlayer` retain their original
export names for compatibility; both use the selected adapter.

Add the model's semantic action set to `ui/action-sets.json`. That registry is
shared with Python, so the three interaction modes and agent tool automatically
use its actions. The player validates the catalog against actual loaded model
capabilities before allowing agent control.

`appearanceStates` separates persistent identity/appearance from actions and
temporary expressions. The dragon declares juvenile form (`变`); DeepSeek
declares its body types, clothing, glasses and accessories. These happen to use
Cubism expression files, but are not actions. Existing saved `expressions` lists
remain compatible; the adapter supplies their semantic classification.

State parameters belong to the user's appearance selection. The renderer resets
their motion baseline, applies the selected state, and preserves its values across
work, touch, physics and agent action layers. Custom expression/pose actions cannot
target these state parameters. Automatic sleep retains the state while resting
hand poses; waking restores poses without restarting retained states. Settings and
the context menu display states separately, and resetting expressions keeps them.

## Import contract

For an external model, add an import normalizer to `kt_live2d/model_adapters.py`
and register `ImportAdapter(normalize=your_normalizer)` in `IMPORT_ADAPTERS`.
It receives the parsed manifest and names
available beside it, and returns a normalized manifest plus any extra work clips.
Validate the capabilities needed by the adapter instead of accepting an unrelated
model with the wrong motions. The settings panel lists supported models from
the runtime registry; the authenticated API uses the Python registry.

Bind the model to its registered adapter in the exported `.model3.json`:

```json
"KohakuTerrarium": { "adapter": "deepseek" }
```

This top-level property names installed adapter code; it cannot load code from
the ZIP. Generated manifests retain this binding. An unknown, malformed or
conflicting declaration is rejected, and the normalizer still validates the
declared model's required assets. Dragon exports declare `dragon`.

Older prepared exports using `Demo.profile` remain supported. For known original
exports only, an adapter may register a nonempty `legacy_files` signature. Without
a declaration, exactly one registered signature must match all its required
filenames. Dragon's original `kohaku` export and DeepSeek's original `Animation/` and
`Exp3/` export are supported this way, so existing ZIPs need no edits. Unknown or ambiguous packages are rejected.
New adapters should use the explicit declaration instead of guessing from names.

`POST /models` identifies and imports the model, returning `model`, `version`
and `bytes`. The older `POST /models/{model}` route remains compatible but also
checks that the requested ID matches the model's binding.

The shared importer accepts one exported model per ZIP, optionally wrapped in a
folder. It checks paths, file count, sizes, required files and JSON structure,
preserves text notices and generates lossless WebP variants with Pillow. All
textures use the same scale: the largest high-quality edge is at most 4096, and
the standard variant halves each dimension. Smaller inputs are never upscaled.
This reduces texture pixels; WebP compression itself only reduces stored bytes.
Full MOC integrity is additionally checked by Cubism when the renderer loads it.

Resources live under
`<KT_CONFIG_DIR>/extension-data/live2d-pet-demo/<local|user-ID>/models/<id>/`.
An import prepares an immutable version and atomically replaces `current` only
after validation and conversion succeed. Prior versions remain available for
renderers already loading them. They can be removed manually after closing those
renderers; imports do not delete user-owned versions. An invalid replacement
leaves the current version untouched.

The frame asks its parent for individual bytes. The parent uses `kt.api` and
the current user's authentication, then transfers buffers to the exact iframe
source/origin/navigation channel. No credentials enter the renderer, no model
resources are published under the package's signed public UI directory, and
switching models cancels pending requests.

## Validation

Rebuild `ui/runtime/player.js` after adapter or runtime changes. Test both texture
qualities, motion/expression capabilities, appearance restoration, mouth overlap,
touch, all three modes and native desktop switching. Add missing/invalid resource
cases and ensure a failed re-import preserves the current model.

Bundled dragon tests run from a clean checkout. To include the private fish in
real-model tests, set `KT_LIVE2D_DEEPSEEK_DIR` to an imported version directory
(containing `high.model3.json` and `standard.model3.json`) before running the
Python and frontend suites. No fish art is required or distributed for CI.
