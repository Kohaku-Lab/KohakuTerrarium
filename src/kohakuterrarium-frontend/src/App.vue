<template>
  <!-- Mobile layout: full-screen, no NavRail -->
  <div v-if="isMobileRoute" class="h-full w-full overflow-hidden bg-warm-50 dark:bg-warm-950">
    <router-view />
    <CommandPalette />
    <ShortcutHelp />
    <ToastCenter />
  </div>
  <!-- Desktop layout, new macro shell (behind kt.shell.enabled flag) -->
  <div v-else-if="shellEnabled" class="h-full overflow-hidden bg-warm-50 dark:bg-warm-950">
    <MacroShell />
    <button v-if="showMobileHint" class="fixed bottom-4 right-4 z-50 px-3 py-2 rounded-lg bg-iolite text-white text-xs shadow-lg flex items-center gap-1.5 hover:bg-iolite/90 transition-colors" @click="switchToMobile">
      <div class="i-carbon-mobile text-sm" />
      <span>{{ t("common.mobileView") }}</span>
    </button>
    <CommandPalette />
    <ShortcutHelp />
    <ToastCenter />
  </div>
  <!-- Desktop layout, legacy NavRail + content (default) -->
  <div v-else class="h-full flex overflow-hidden bg-warm-50 dark:bg-warm-950">
    <NavRail />
    <main class="flex-1 overflow-hidden">
      <router-view />
    </main>
    <!-- "Switch to mobile" button — only shown when user manually left mobile on a small screen -->
    <button v-if="showMobileHint" class="fixed bottom-4 right-4 z-50 px-3 py-2 rounded-lg bg-iolite text-white text-xs shadow-lg flex items-center gap-1.5 hover:bg-iolite/90 transition-colors" @click="switchToMobile">
      <div class="i-carbon-mobile text-sm" />
      <span>{{ t("common.mobileView") }}</span>
    </button>
    <CommandPalette />
    <ShortcutHelp />
    <ToastCenter />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue"

import CommandPalette from "@/components/chrome/CommandPalette.vue"
import ShortcutHelp from "@/components/chrome/ShortcutHelp.vue"
import ToastCenter from "@/components/chrome/ToastCenter.vue"
import NavRail from "@/components/layout/NavRail.vue"
import MacroShell from "@/components/shell/MacroShell.vue"
import { getUIVersion } from "@/utils/uiVersion"
import { useArtifactDetector } from "@/composables/useArtifactDetector"
import { useAutoTriggers } from "@/composables/useAutoTriggers"
import { useBuiltinCommands } from "@/composables/useBuiltinCommands"
import { useKeyboardShortcuts } from "@/composables/useKeyboardShortcuts"
import { useInstancesStore } from "@/stores/instances"
import { useLocaleStore } from "@/stores/locale"
import { useThemeStore } from "@/stores/theme"
import { useI18n } from "@/utils/i18n"
import { toDesktopRoute, toMobileRoute } from "@/utils/routes"
import { getHybridPrefSync, removeHybridPref, setHybridPref } from "@/utils/uiPrefs"

const MOBILE_WIDTH = 768
const route = useRoute()
const router = useRouter()
const locale = useLocaleStore()
const { t } = useI18n()

const isMobileRoute = computed(() => route.path.startsWith("/mobile"))
const windowWidth = ref(window.innerWidth)
const forceDesktop = ref(getHybridPrefSync("kt-force-desktop", false) === true)

// UI version — Classic (v1) vs Workspace (v2). Both shells coexist
// indefinitely; user picks via the footer toggle in either rail or
// via Settings. Default is v1 this version (backward-compat); the
// default flips to v2 once it reaches feature parity.
const shellEnabled = ref(getUIVersion() === "v2")
if (typeof window !== "undefined") {
  window.addEventListener("kt:ui-version-changed", (e) => {
    shellEnabled.value = e?.detail === "v2"
  })
  // Cross-tab: another window flipping the pref should propagate here.
  window.addEventListener("storage", (e) => {
    if (e.key === "kt-ui-version" || e.key === "kt.shell.enabled") {
      shellEnabled.value = getUIVersion() === "v2"
    }
  })
}

// Show "switch to mobile" hint only when: small screen + user explicitly left mobile + on desktop route
const showMobileHint = computed(() => windowWidth.value < MOBILE_WIDTH && forceDesktop.value && !isMobileRoute.value)

function onResize() {
  windowWidth.value = window.innerWidth
}

onMounted(() => window.addEventListener("resize", onResize))
onUnmounted(() => window.removeEventListener("resize", onResize))

// Auto-redirect to mobile on small screens (unless user explicitly chose desktop)
watch(
  [() => route.path, windowWidth],
  () => {
    if (forceDesktop.value) return
    if (windowWidth.value >= MOBILE_WIDTH) return
    if (isMobileRoute.value) return

    // Map desktop route → mobile equivalent
    const mobileRoute = toMobileRoute(route.path)
    if (mobileRoute) router.replace(mobileRoute)
  },
  { immediate: true },
)

function switchToMobile() {
  forceDesktop.value = false
  removeHybridPref("kt-force-desktop")
  const mobileRoute = toMobileRoute(route.path)
  if (mobileRoute) router.replace(mobileRoute)
}

// Exposed for MobileShell to call
function switchToDesktop() {
  forceDesktop.value = true
  setHybridPref("kt-force-desktop", true)
  const desktopRoute = toDesktopRoute(route.path)
  router.replace(desktopRoute)
}

provide("switchToDesktop", switchToDesktop)

const theme = useThemeStore()
theme.init()
locale.init()

// Sync mobile mode flag to theme store so it applies the correct zoom level.
watch(isMobileRoute, (m) => theme.setMobileMode(m), { immediate: true })

const instances = useInstancesStore()
instances.fetchAll()

useKeyboardShortcuts()
useBuiltinCommands()
useAutoTriggers()
useArtifactDetector()
</script>
