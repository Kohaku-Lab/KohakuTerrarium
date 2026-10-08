/**
 * The shell's one way into Studio: switching apps, and the Studio surface
 * and rail navigator the shell shows in Studio mode, loaded when first shown.
 */

import { defineAsyncComponent } from "vue"

import { setAppMode } from "@/components/shell/rail/useAppMode"
import { useTabsStore } from "@/stores/tabs"

export { goStudio } from "@/components/studio/app/useStudioRoute"

export const StudioApp = defineAsyncComponent(() => import("@/components/studio/app/StudioApp.vue"))
export const StudioRailNav = defineAsyncComponent(
  () => import("@/components/studio/app/rail/StudioRailNav.vue"),
)

/** Go to an app. Terrarium brings its home tab (the lab) forward; Studio is its own surface. */
export function useGoToApp() {
  const tabs = useTabsStore()
  return function goTo(mode) {
    setAppMode(mode)
    if (mode === "terrarium") tabs.openTab({ kind: "dashboard", id: "dashboard" })
  }
}
