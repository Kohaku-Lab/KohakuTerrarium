/**
 * Resolve the layout store the user is actually looking at.
 *
 * App-root composables (keyboard shortcuts, the command palette, auto
 * triggers) run outside any AttachTab, so a bare ``useLayoutStore()`` there
 * lands on the ``layout:default`` singleton while the visible attach tab
 * reads its own per-scope store. Resolving through the tabs store keeps
 * preset switches on the tab in front of the user.
 */

import { useLayoutStore } from "@/stores/layout"
import { useTabsStore } from "@/stores/tabs"

export function activeLayoutScope() {
  const tabs = useTabsStore()
  const tab = tabs.activeTab
  if (tab && tab.kind === "attach" && tab.target) return tab.target
  return null
}

export function activeLayoutStore() {
  return useLayoutStore(activeLayoutScope())
}
