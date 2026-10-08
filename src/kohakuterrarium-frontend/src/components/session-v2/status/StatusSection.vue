<template>
  <section class="kt-v2-card overflow-hidden" :data-test="empty ? `status-empty-${id}` : `status-section-${id}`">
    <header class="kt-v2-line flex items-center gap-2 px-4 h-10 border-b">
      <span :class="icon" class="text-warm-500" />
      <h3 class="text-sm font-semibold text-warm-700 dark:text-warm-200">{{ title }}</h3>
      <span v-if="count != null" class="text-xs font-mono text-warm-400">{{ count }}</span>
      <span class="flex-1" />
      <slot name="actions" />
    </header>
    <div v-if="empty" class="flex flex-col items-center justify-center gap-1.5 py-8 text-xs text-warm-400">
      <span :class="icon" class="text-xl opacity-60" />
      <span>{{ emptyLabel }}</span>
    </div>
    <div v-else class="overflow-x-auto"><slot /></div>
  </section>
</template>

<script setup>
/** A whole-width Status card; with nothing to show it stays in place and says so, so the page keeps its shape. */
defineProps({
  id: { type: String, required: true },
  title: { type: String, required: true },
  icon: { type: String, default: "i-carbon-list" },
  count: { type: Number, default: null },
  empty: { type: Boolean, default: false },
  emptyLabel: { type: String, default: "" },
})
</script>
