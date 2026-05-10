<template>
  <div class="h-full w-full flex flex-col overflow-hidden bg-warm-50 dark:bg-warm-950">
    <CompactHeader @open-rail="railOpen = true" />
    <!-- `flex flex-col` here is load-bearing: TabContent uses
         `flex-1` to claim its parent's height, which only works when
         the parent itself is a flex container. Without this, panels
         that use `h-full overflow-y-auto` (Dashboard, Settings, …)
         resolve to zero height and cannot scroll. -->
    <div class="flex-1 min-h-0 flex flex-col overflow-hidden">
      <TabContent />
    </div>
    <CompactRailDrawer v-model:open="railOpen" />
  </div>
</template>

<script setup>
import { ref } from "vue"

import CompactHeader from "@/components/shell/CompactHeader.vue"
import CompactRailDrawer from "@/components/shell/CompactRailDrawer.vue"
import TabContent from "@/components/shell/TabContent.vue"

const railOpen = ref(false)
</script>
