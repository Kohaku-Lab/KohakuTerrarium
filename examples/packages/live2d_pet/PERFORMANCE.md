# Live2D resource budgets and validation

The extension targets 30 FPS and supports two texture quality levels. Both
levels use the same model, UV layout, physics and animation data. Smaller
textures reduce decoded texture pixels; lossless WebP reduces storage and
transfer size without reducing the pixel budget at a given resolution.

## Resource budgets

| Model | High quality | Performance | RGBA texture pixels: high / performance |
| --- | --- | --- | --- |
| Dragon | 3 × 4096² | 3 × 2048² | 192 / 48 MiB |
| DeepSeek | Largest edge 4096 | Largest edge 2048 | 148 / 37 MiB |

DeepSeek also uses two 1024² mask targets, adding 8 MiB of RGBA pixels. Canvas,
decoding, model and driver allocations are additional. These figures are not
process memory or total GPU memory.

The distributable extension is approximately 44.4 MiB, or 32.8 MiB as a ZIP.
It includes the dragon's 2K/4K resources and the Cubism runtime. DeepSeek's
adapter is included, but its artwork is imported into the current KT user's
private data directory. SDK notices and model attribution remain applicable.

## Local measurements

Measured on 2026-11-03 in the native KT Source WKWebView on an Apple M4 Pro with
48 GiB RAM. The dragon used 160% size, juvenile appearance and extended wings;
DeepSeek used 110% size, body type 1, blush and natural idle. Physics, gaze and
Debug were enabled; automatic sleep was disabled. One model was visible at a
time, without work-action rehearsals. Each warmed-up configuration was sampled
for 25 seconds without simultaneous builds or tests.

| Configuration | Texture MiB | CPU mean | RSS mean / max MiB | Final one-second FPS | CPU frame mean / P95 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Dragon 4K | 192 | 27.6% | 557 / 762 | 28 | 4.1 / 5.0 |
| Dragon 2K | 48 | 29.4% | 736 / 736 | 24 | 4.2 / 5.0 |
| DeepSeek 4K | 148 | 29.9% | 758 / 918 | 28 | 6.5 / 9.0 |
| DeepSeek 2K | 37 | 30.4% | 423 / 435 | 25 | 6.7 / 9.0 |

CPU is the combined CPU-time delta of KT's WebContent and WebKit GPU processes;
100% represents one CPU core. The no-pet baseline was approximately 0.3%.
RSS is those processes' combined resident memory, including caches and driver
allocations. Sequential switching delays reclamation: the larger Dragon 2K RSS
in this sample does not establish a higher intrinsic memory cost than 4K.

Frame cost covers updates, drawing submissions and crop calculations, not
asynchronous GPU execution. The final ten-second windows had no frame intervals
over 100 ms. These short measurements did not show a consistent CPU reduction
from 2K: mesh, physics and animation work stay the same. They are not independent
cold starts, long-term leak tests or results for other hardware.

## Current validation scope

Native Tauri acceptance on 2026-11-05 confirmed:

- A malformed ZIP reports an import error and preserves existing resources.
  A valid DeepSeek ZIP imports through settings, selects the model and survives
  a restart. Both generated quality levels render.
- The adapter list is independent of resource installation. The import entry is
  **导入 L2D 模型**; installed resources and available adapters have distinct status.
- Switching between DeepSeek and Dragon preserves each model's
  appearance and size. The three interaction modes switch without launching a
  second background agent. The dedicated entry reopens the existing companion.
- GLM 5.3 Flash completed ordinary-expression and dedicated-companion turns
  with bounded `l2d` actions. The imported model's mouth varied during streamed
  reply text and returned to zero after completion.
- Normal and Debug rendering work. Dragon renders in the separate desktop
  window, and the right-click close command hides it and releases control.

Automated tests additionally exercise actual Cubism models: nod motion and
return, mouth/action overlap, expression restoration, physics, bounds and
capability validation. Import tests cover missing files, traversal, symlinks,
archive limits, atomic replacement and authenticated user isolation. Host tests
cover old-frame messages, interrupted streams, mode/session changes and stale
bindings. See the [README](README.md#validation) for reproducible commands.

The final focused suites, Studio journey, Python guards, formatting, frontend
build and native overlay tests passed. The full frontend run retained one
previously observed failure in `chat.historyPage.integration.test.js:515`
(expected two running jobs, received three); that history code is unchanged.
Full-suite green is not claimed. Multi-monitor arrangements, different desktop
scale factors and long-running stability remain outside this acceptance scope.

The measurements above predate the switch to private-only Dragon distribution.
Both models now require import. Texture dimensions and renderer behavior are
unchanged; these measurements are not a new benchmark of the import path.
