/**
 * useDensity — single source of truth for layout density mode.
 *
 * Density is one of: "compact" | "regular" | "expansive". It is
 * derived from viewport width by default; the user can pin it to any
 * value via `setOverride`. The shell branches on this value:
 *   - compact   → single-panel + tab-bar shell (multi-panel system off)
 *   - regular   → full MacroShell with the tree-based preset system
 *   - expansive → same as regular today; reserved for extra-wide variants
 *
 * Thresholds (default):
 *   < 768px            → compact
 *   768..1279px        → regular
 *   ≥ 1280px           → expansive
 *
 * Override is persisted to the hybrid pref `kt-density-override`. The
 * sentinel value `auto` means "follow the viewport"; any other valid
 * density pins the value.
 *
 * Module-level singleton: every consumer shares one reactive cell, so
 * setting the override anywhere updates the shell everywhere.
 */

import { computed, ref } from "vue"

import { getHybridPrefSync, removeHybridPref, setHybridPref } from "@/utils/uiPrefs"

const COMPACT_MAX = 768 // < 768 → compact
const REGULAR_MAX = 1280 // 768..1279 → regular; ≥ 1280 → expansive

const OVERRIDE_KEY = "kt-density-override"
const VALID_DENSITIES = ["compact", "regular", "expansive"]
const VALID_OVERRIDES = ["auto", ...VALID_DENSITIES]

const hasWindow = typeof window !== "undefined"

function readViewportWidth() {
  return hasWindow ? window.innerWidth : 1280
}

function deriveViewportDensity(width) {
  if (width < COMPACT_MAX) return "compact"
  if (width < REGULAR_MAX) return "regular"
  return "expansive"
}

function readStoredOverride() {
  const raw = getHybridPrefSync(OVERRIDE_KEY, "auto")
  return VALID_OVERRIDES.includes(raw) ? raw : "auto"
}

// Reactive cells are module-level so all consumers share state, but
// we lazy-init their values + attach listeners on first use. This
// keeps `import` side-effect-free, which matters for tests that need
// to stub `localStorage` *before* the module reads from it.
const _viewportWidth = ref(0)
const _override = ref("auto")

let _initialized = false

function _initialize() {
  if (_initialized) return
  _initialized = true
  _viewportWidth.value = readViewportWidth()
  _override.value = readStoredOverride()
  if (hasWindow) {
    window.addEventListener("resize", () => {
      _viewportWidth.value = window.innerWidth
    })
    // Cross-tab sync — another window changing the override should
    // propagate here. Mirrors the kt-ui-version pattern in App.vue.
    window.addEventListener("storage", (e) => {
      if (e.key === OVERRIDE_KEY) {
        _override.value = readStoredOverride()
      }
    })
  }
}

const _viewportDensity = computed(() => deriveViewportDensity(_viewportWidth.value))

const _density = computed(() =>
  _override.value !== "auto" ? _override.value : _viewportDensity.value,
)

function setOverride(value) {
  if (!VALID_OVERRIDES.includes(value)) return
  _initialize()
  _override.value = value
  if (value === "auto") removeHybridPref(OVERRIDE_KEY)
  else setHybridPref(OVERRIDE_KEY, value)
}

export function useDensity() {
  _initialize()
  return {
    density: _density,
    viewportDensity: _viewportDensity,
    override: _override,
    setOverride,
    isCompact: computed(() => _density.value === "compact"),
    isRegular: computed(() => _density.value === "regular"),
    isExpansive: computed(() => _density.value === "expansive"),
  }
}

/** Numeric rank for comparing densities (compact < regular < expansive). */
export function densityRank(density) {
  const i = VALID_DENSITIES.indexOf(density)
  return i < 0 ? 0 : i
}

/** True iff `current` density is at least `required`. */
export function meetsDensity(current, required) {
  return densityRank(current) >= densityRank(required)
}

export const DENSITIES = VALID_DENSITIES
export const DENSITY_OVERRIDES = VALID_OVERRIDES

// Test-only — reset module state so unit tests can exercise the
// composable in isolation. Re-reads the viewport + stored override
// so tests that stub localStorage *between* runs see fresh values.
export function _resetDensityForTests() {
  _initialized = false
  _viewportWidth.value = readViewportWidth()
  _override.value = "auto"
}
