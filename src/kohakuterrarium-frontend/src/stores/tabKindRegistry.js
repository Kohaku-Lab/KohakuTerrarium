/**
 * Tab-kind registry. Three reactive maps the macro shell consults:
 *
 *   tabKinds            → kind → { component, capabilities }
 *   inspectorInnerTabs  → id → { component, label, order }
 *   railGroups          → id → { component, order }
 *
 * The registries are populated at app boot by built-in registrations.
 * Lazy plugins can register entries via the same call without
 * modifying core shell code (D7 of the UI design).
 *
 * D7 architectural constraint: NO tab-kind-aware code outside
 * `components/shell/TabContent.vue` and the rail-group files.
 * Discriminating by kind elsewhere is a coupling smell.
 */

import { reactive } from "vue"

export const tabKinds = reactive(new Map())
export const inspectorInnerTabs = reactive(new Map())
export const railGroups = reactive(new Map())

/** The 10 built-in kinds, listed for URL parser sync. */
export const BUILTIN_KINDS = [
  "dashboard",
  "attach",
  "inspector",
  "session-viewer",
  "saved-sessions",
  "stats",
  "studio-editor",
  "catalog",
  "settings",
  "code-editor",
]

/**
 * Register a tab kind.
 *
 * `minDensity` (optional, default "compact") gates rendering at
 * narrow viewports. Tab kinds that fundamentally need horizontal room
 * (Studio's file-tree + Monaco, Catalog's master-detail browse)
 * declare "regular"; the shell renders an UnderDensityPlaceholder
 * with an override button instead of the real component when current
 * density falls below this threshold.
 */
export function registerTabKind({ kind, component, capabilities = {}, minDensity = "compact" }) {
  if (tabKinds.has(kind)) {
    console.warn(`tab kind ${kind} already registered; overwriting`)
  }
  tabKinds.set(kind, { component, capabilities, minDensity })
}

export function registerInspectorInnerTab({ id, component, label, order = 100 }) {
  inspectorInnerTabs.set(id, { component, label, order })
}

export function registerRailGroup({ id, component, order = 100 }) {
  railGroups.set(id, { component, order })
}

/** Lookup helper used by TabContent.vue. */
export const tabKindRegistry = {
  has(kind) {
    return tabKinds.has(kind)
  },
  get(kind) {
    return tabKinds.get(kind)
  },
}
