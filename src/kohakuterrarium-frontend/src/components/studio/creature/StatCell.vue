<template>
  <div class="px-2.5 py-1.5 rounded bg-warm-100 dark:bg-warm-950 border border-warm-200/70 dark:border-warm-800/70">
    <div class="text-[10px] uppercase tracking-wider text-warm-500">
      {{ label }}
    </div>
    <div class="text-sm font-semibold text-warm-800 dark:text-warm-200 tabular-nums">
      {{ value }}
    </div>
  </div>
</template>

<script setup>
defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], default: 0 },
})
</script>
