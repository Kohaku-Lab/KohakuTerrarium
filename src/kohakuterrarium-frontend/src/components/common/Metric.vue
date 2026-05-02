<template>
  <div class="flex flex-col gap-1">
    <span class="text-[10px] uppercase tracking-wider text-warm-500">{{ label }}</span>
    <span class="text-xl font-medium text-warm-800 dark:text-warm-200 tabular-nums">
      {{ value }}
    </span>
    <span v-if="hint" class="text-[10px] text-warm-400">{{ hint }}</span>
  </div>
</template>

<script setup>
defineProps({
  label: { type: String, required: true },
  value: { type: String, required: true },
  hint: { type: String, default: "" },
})
</script>
