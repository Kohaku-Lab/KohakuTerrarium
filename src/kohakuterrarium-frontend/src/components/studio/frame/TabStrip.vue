<template>
  <div class="flex items-stretch bg-warm-100 dark:bg-warm-950 border-b border-warm-200 dark:border-warm-800 overflow-x-auto scrollbar-none">
    <button v-for="tab in tabs" :key="tab.id" :class="['group relative flex items-center gap-1.5 pl-3 pr-2 h-9 text-xs whitespace-nowrap border-r border-warm-200 dark:border-warm-800 transition-colors', tab.id === active ? 'bg-warm-50 dark:bg-warm-900 text-warm-900 dark:text-warm-100' : 'text-warm-500 dark:text-warm-400 hover:bg-warm-50/50 dark:hover:bg-warm-900/50 hover:text-warm-700 dark:hover:text-warm-200']" @click="$emit('select', tab.id)" @mousedown.middle.prevent="!tab.pinned && $emit('close', tab.id)">
      <div v-if="tab.icon" :class="[tab.icon, 'text-sm shrink-0']" />
      <span>{{ tab.label }}</span>
      <span v-if="tab.dirty" class="w-1.5 h-1.5 rounded-full bg-iolite shrink-0" aria-label="unsaved" />
      <button v-if="!tab.pinned" class="ml-0.5 w-4 h-4 inline-flex items-center justify-center rounded hover:bg-warm-200 dark:hover:bg-warm-700 opacity-0 group-hover:opacity-100 transition-opacity text-warm-500" :title="`Close ${tab.label}`" @click.stop="$emit('close', tab.id)">
        <div class="i-carbon-close text-[10px]" />
      </button>
      <!-- active indicator -->
      <div v-if="tab.id === active" class="absolute left-0 right-0 top-0 h-0.5 bg-iolite" aria-hidden="true" />
    </button>
    <div class="flex-1 border-b border-warm-200 dark:border-warm-800" />
  </div>
</template>

<script setup>
/**
 * Minimal editor-style tab strip.
 *
 * tabs: [{ id, label, icon?, dirty?, pinned? }]
 *   - pinned tabs don't show a close button and ignore middle-click close
 *   - dirty tabs show a small dot
 *   - icon is a UnoCSS icon class (e.g. "i-carbon-document")
 */
defineProps({
  tabs: { type: Array, required: true },
  active: { type: String, default: "" },
})

defineEmits(["select", "close"])
</script>

<style scoped>
.scrollbar-none::-webkit-scrollbar {
  display: none;
}
.scrollbar-none {
  scrollbar-width: none;
}
</style>
