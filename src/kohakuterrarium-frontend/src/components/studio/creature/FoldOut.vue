<template>
  <div class="rounded-lg border border-warm-200 dark:border-warm-800 bg-white dark:bg-warm-900 overflow-hidden">
    <button class="w-full px-4 py-2 flex items-center gap-2 text-left hover:bg-warm-50 dark:hover:bg-warm-800/60 transition-colors" @click="open = !open">
      <div :class="[open ? 'i-carbon-chevron-down' : 'i-carbon-chevron-right', 'text-sm text-warm-500 shrink-0']" />
      <div v-if="icon" :class="[icon, 'text-sm text-iolite dark:text-iolite-light shrink-0']" />
      <span class="flex-1 text-sm font-medium text-warm-800 dark:text-warm-200">
        {{ title }}
      </span>
      <span v-if="count != null" class="text-[11px] text-warm-500 dark:text-warm-500 tabular-nums"> ({{ count }}) </span>
    </button>
    <div v-if="open" class="px-4 pb-3 pt-1">
      <slot />
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue"

defineProps({
  title: { type: String, required: true },
  icon: { type: String, default: "" },
  count: { type: Number, default: null },
  defaultOpen: { type: Boolean, default: false },
})

const open = ref(false)
</script>
