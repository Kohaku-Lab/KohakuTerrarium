<template>
  <section>
    <header v-if="title || $slots.action" class="flex items-end gap-3 mb-2">
      <div class="flex-1 min-w-0">
        <h3 v-if="title" class="kt-text-caption uppercase tracking-wider text-warm-500">{{ title }}</h3>
        <p v-if="description" class="kt-text-caption text-warm-500 mt-0.5">{{ description }}</p>
      </div>
      <slot name="action" />
    </header>
    <div class="kt-v2-card divide-y divide-[var(--v2-line)] overflow-hidden"><slot /></div>
  </section>
</template>

<script setup>
/** A captioned card of settings rows (or any content) on one topic. */
defineProps({
  title: { type: String, default: "" },
  description: { type: String, default: "" },
})
</script>
