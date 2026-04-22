<template>
  <div ref="root" class="h-full flex flex-col">
    <div v-if="title" class="shrink-0 px-3 py-2 border-b border-warm-200 dark:border-warm-800 text-[11px] uppercase tracking-wider font-medium text-warm-500 dark:text-warm-400">
      {{ title }}
    </div>
    <div class="flex-1 min-h-0 overflow-y-auto p-3">
      <slot />
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from "vue"

import { useStudioUiStore } from "@/stores/studio/ui"

defineProps({
  title: { type: String, default: "" },
})

// DetailPanel is the "destination" for safe-triangle hover preservation.
// Registering the root element lets the UI store keep hover state
// alive while the mouse is en route to (or inside) this panel —
// so buttons here can actually be clicked.
const root = ref(null)
const ui = useStudioUiStore()

onMounted(() => {
  if (root.value) ui.registerHoverDestination(root.value)
})

onUnmounted(() => {
  ui.registerHoverDestination(null)
})
</script>
