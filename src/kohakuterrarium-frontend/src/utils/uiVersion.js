/**
 * UI version selection — first-class user choice between two coexisting
 * shells:
 *
 *   v1  "Classic"    — NavRail + page-per-route. The original UI.
 *   v2  "Workspace"  — MacroShell with rail-of-running-targets and
 *                       multi-tab content area. New in this version.
 *
 * Both shells live indefinitely; the user picks via the footer toggle
 * in either rail or via the Settings page. Default is v1 this version
 * (backward compat); flipped to v2 by default in a future release once
 * v2 reaches feature parity.
 *
 * Storage key: ``kt-ui-version`` via the hybrid pref helper. Legacy
 * ``kt.shell.enabled = "true"`` is honoured as v2 for one release.
 */

import { getHybridPrefSync, setHybridPref } from "@/utils/uiPrefs"

const KEY = "kt-ui-version"
const DEFAULT_VERSION = "v1"

export const UI_VERSIONS = [
  {
    id: "v1",
    label: "Classic",
    description: "NavRail with page-per-route. Battle-tested; the original UI for KohakuTerrarium.",
  },
  {
    id: "v2",
    label: "Workspace",
    description:
      "Macro shell with multi-tab workspace, surface picker (Chat / Inspector) per running target, and a unified dashboard.",
  },
]

const VERSION_IDS = UI_VERSIONS.map((v) => v.id)

/** Read the current UI version from prefs. Honours legacy flag. */
export function getUIVersion() {
  const explicit = getHybridPrefSync(KEY, null)
  if (typeof explicit === "string" && VERSION_IDS.includes(explicit)) {
    return explicit
  }
  // Backward compatibility: kt.shell.enabled=true → v2
  try {
    if (
      typeof localStorage !== "undefined" &&
      localStorage.getItem("kt.shell.enabled") === "true"
    ) {
      return "v2"
    }
  } catch {
    /* swallow — privacy mode etc. */
  }
  return DEFAULT_VERSION
}

/** Persist the UI version selection and notify listeners in this window. */
export function setUIVersion(version) {
  if (!VERSION_IDS.includes(version)) {
    throw new Error(`unknown UI version: ${version}`)
  }
  setHybridPref(KEY, version)
  // Drop the legacy shim so it does not shadow on next read.
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.removeItem("kt.shell.enabled")
    }
  } catch {
    /* swallow */
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("kt:ui-version-changed", { detail: version }))
  }
}

/** Convenience: cycle through the available versions in order. */
export function cycleUIVersion() {
  const idx = VERSION_IDS.indexOf(getUIVersion())
  const next = VERSION_IDS[(idx + 1) % VERSION_IDS.length]
  setUIVersion(next)
  return next
}
