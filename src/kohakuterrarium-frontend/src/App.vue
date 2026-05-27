<template>
  <!-- v2 macro shell only — handles every viewport size via density
       auto-detect. The dedicated v1 NavRail and the /mobile/* page
       tree both retired in this release; bookmarks to the old URLs
       are caught by the router guard in main.js and rewritten to
       canonical equivalents. -->
  <div class="h-full overflow-hidden bg-warm-50 dark:bg-warm-950">
    <AuthGate>
      <MacroShell />
    </AuthGate>
    <CommandPalette />
    <ShortcutHelp />
    <ToastCenter />
    <HostPickerModal :open="hostPickerOpen" @close="hostPickerOpen = false" />
    <AdminTokenModal />
  </div>
</template>

<script setup>
import { ref, watch } from "vue"

import AdminTokenModal from "@/components/auth/AdminTokenModal.vue"
import AuthGate from "@/components/auth/AuthGate.vue"
import CommandPalette from "@/components/chrome/CommandPalette.vue"
import ShortcutHelp from "@/components/chrome/ShortcutHelp.vue"
import ToastCenter from "@/components/chrome/ToastCenter.vue"
import HostPickerModal from "@/components/host-picker/HostPickerModal.vue"
import MacroShell from "@/components/shell/MacroShell.vue"
import { useArtifactDetector } from "@/composables/useArtifactDetector"
import { useAutoTriggers } from "@/composables/useAutoTriggers"
import { useBuiltinCommands } from "@/composables/useBuiltinCommands"
import { useConnectIntent } from "@/composables/useConnectIntent"
import { useDensity } from "@/composables/useDensity"
import { useKeyboardShortcuts } from "@/composables/useKeyboardShortcuts"
import { useAuthStore } from "@/stores/auth"
import { useHostsStore } from "@/stores/hosts"
import { useInstancesStore } from "@/stores/instances"
import { useLocaleStore } from "@/stores/locale"
import { useThemeStore } from "@/stores/theme"

const theme = useThemeStore()
const locale = useLocaleStore()
const { isCompact } = useDensity()

theme.init()
locale.init()

// Theme keeps separate desktop/mobile zoom levels; sync the active
// one off the density signal (compact = mobile zoom, otherwise =
// desktop zoom). v1 used route-based detection; v2 derives it from
// the same density composable the shell does.
watch(isCompact, (compact) => theme.setMobileMode(compact), { immediate: true })

// Probe the active host's auth capabilities on boot so the AuthGate
// + interceptor know which layers are enabled before the first
// /api/* call goes out.  Re-probes happen automatically on host
// switch via the AuthGate's own watcher.
const auth = useAuthStore()
const hostsStoreInstance = useHostsStore()
auth.fetch()
watch(
  () => hostsStoreInstance.activeHostId,
  () => auth.fetch(),
)

const instances = useInstancesStore()
instances.fetchAll()

useKeyboardShortcuts()
useBuiltinCommands()
useAutoTriggers()
useArtifactDetector()

// Host picker state — accessed by ``HostStatusChip`` via a global
// event so we don't have to thread a prop through every shell
// component.  The chip dispatches ``kt-open-host-picker`` on click.
const hostPickerOpen = ref(false)

if (typeof window !== "undefined") {
  window.addEventListener("kt-open-host-picker", () => {
    hostPickerOpen.value = true
  })
  // Auto-open when an Android ``ktconnect://`` deep-link is
  // queued — the modal's own watcher will consume + apply the URI.
  const { pendingUri } = useConnectIntent()
  watch(pendingUri, (uri) => {
    if (uri) hostPickerOpen.value = true
  })
}
</script>
