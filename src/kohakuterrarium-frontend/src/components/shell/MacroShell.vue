<template>
  <CompactShell v-if="isCompact" />
  <div v-else class="h-full flex overflow-hidden bg-warm-50 dark:bg-warm-950">
    <RailPane />
    <div class="flex-1 flex flex-col overflow-hidden">
      <TabStrip />
      <TabContent />
    </div>
  </div>
</template>

<script setup>
import { onMounted } from "vue"

import CompactShell from "@/components/shell/CompactShell.vue"
import RailPane from "@/components/shell/RailPane.vue"
import TabStrip from "@/components/shell/TabStrip.vue"
import TabContent from "@/components/shell/TabContent.vue"
import { useDensity } from "@/composables/useDensity"
import { useTabsStore } from "@/stores/tabs"
import { useInstancesStore } from "@/stores/instances"
import { useTabPersistence } from "@/composables/useTabPersistence"
import { registerBuiltinTabKinds } from "@/components/shell/registerBuiltins"

const tabs = useTabsStore()
const instances = useInstancesStore()
const { isCompact } = useDensity()

// Register tab-kind components — only the kinds wired up at the
// current phase. Phase 2 has none; Phase 3+ adds Inspector, Dashboard,
// AttachTab, etc. Calling this here is idempotent across HMR.
registerBuiltinTabKinds()

// Hydrate from + persist to localStorage.
useTabPersistence()

onMounted(() => {
  // Migrate per-instance preset memory once. Idempotent.
  tabs.migrateLayoutPresetKeys()

  // Default Dashboard tab is added by useTabPersistence after it
  // finishes hydrating from localStorage (so we know whether storage
  // had tabs first).

  // Kick off instance polling so the rail's Attached group has data.
  if (typeof instances.startPolling === "function") {
    instances.startPolling()
  } else {
    instances.fetchAll()
  }
})
</script>
