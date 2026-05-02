/**
 * Studio navigation indirection — works in both v1 (route-driven) and
 * v2 (tab-driven) contexts.
 *
 * v1 path: pages use vue-router; the composable's defaults push routes.
 * v2 path: ``StudioEditorTab.vue`` provides ``studioNav`` via Vue's
 *          provide/inject; navigation opens new tabs instead.
 *
 * All studio pages call ``useStudioNav()`` and use the returned
 * methods instead of touching ``router.push`` directly. That keeps a
 * single navigation surface across both shells.
 */

import { inject } from "vue"
import { useRouter } from "vue-router"

const INJECT_KEY = "studioNav"

/** v2 host injects this object via ``provide("studioNav", ...)``. */
export function provideStudioNav(impl) {
  // Re-exported for convenience; callers do `provide("studioNav", impl)`
  // directly using Vue's `provide`.
  return { key: INJECT_KEY, value: impl }
}

export function useStudioNav() {
  const overridden = inject(INJECT_KEY, null)
  const router = useRouter?.()

  function pushRoute(path) {
    if (router) router.push(path)
  }

  return {
    openHome() {
      if (overridden?.openHome) return overridden.openHome()
      pushRoute("/studio")
    },
    openWorkspace(rootPath) {
      if (overridden?.openWorkspace) return overridden.openWorkspace(rootPath)
      pushRoute(`/studio/workspace/${encodeURIComponent(rootPath)}`)
    },
    openCreature(name, opts = {}) {
      if (overridden?.openCreature) return overridden.openCreature(name, opts)
      pushRoute(`/studio/creature/${encodeURIComponent(name)}`)
    },
    openModule(kind, name, opts = {}) {
      if (overridden?.openModule) return overridden.openModule(kind, name, opts)
      pushRoute(`/studio/module/${kind}/${encodeURIComponent(name)}`)
    },
  }
}

export const STUDIO_NAV_INJECT_KEY = INJECT_KEY
