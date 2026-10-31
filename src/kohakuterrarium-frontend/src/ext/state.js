/**
 * Shared reactive state of package UI extensions on the active host.
 *
 * ``packages`` mirrors ``GET /api/extensions/ui`` plus a per-package
 * load ``status``: idle | loading | loaded | error | incompatible | disabled.
 * ``components`` maps a contribution key to the component its package
 * registered. ``runtime.ensureLoaded`` is installed by the loader.
 */

import { reactive } from "vue"

export const extState = reactive({
  hostKey: "",
  loading: false,
  error: "",
  packages: [],
})

export const contributionComponents = reactive(new Map())

export const runtime = {
  ensureLoaded: async (_pkg) => {
    throw new Error("UI extension loader is not started")
  },
  loadFrameDocument: async (_pkg) => {
    throw new Error("UI extension loader is not started")
  },
}

/** Global key of a contribution: ``ext:<package>/<id>``. */
export function contributionKey(pkg, id) {
  return `ext:${pkg}/${id}`
}

/** Split ``ext:<package>/<id>`` (or ``<package>/<id>``) into its parts, or ``null``. */
export function parseContributionKey(key) {
  const body = String(key || "").replace(/^ext:/, "")
  const slash = body.lastIndexOf("/")
  if (slash <= 0 || slash === body.length - 1) return null
  return { pkg: body.slice(0, slash), id: body.slice(slash + 1) }
}

export function findPackage(pkg) {
  return extState.packages.find((p) => p.package === pkg) || null
}

export function findContribution(pkg, id) {
  return findPackage(pkg)?.contributes?.find((c) => c.id === id) || null
}
