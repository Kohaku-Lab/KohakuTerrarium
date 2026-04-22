<template>
  <label class="block">
    <div v-if="label || $slots.label" class="flex items-baseline justify-between mb-1">
      <span class="text-xs font-medium text-warm-700 dark:text-warm-300">
        <slot name="label">{{ label }}</slot>
        <span v-if="required" class="text-coral ml-0.5">*</span>
      </span>
      <span v-if="inheritBadge" :class="['text-[10px] font-medium px-1.5 py-0.5 rounded', inheritBadge === 'inherit' && 'bg-warm-200/60 dark:bg-warm-800/60 text-warm-500 dark:text-warm-400', inheritBadge === 'override' && 'bg-iolite/15 text-iolite dark:text-iolite-light', inheritBadge === 'new' && 'bg-sage/20 text-sage-shadow dark:text-sage']">
        {{ inheritBadge }}
      </span>
    </div>
    <div><slot /></div>
    <p v-if="hint" class="mt-1 text-[11px] text-warm-500 dark:text-warm-500">
      {{ hint }}
    </p>
    <p v-if="error" class="mt-1 text-[11px] text-coral">{{ error }}</p>
  </label>
</template>

<script setup>
defineProps({
  label: { type: String, default: "" },
  hint: { type: String, default: "" },
  error: { type: String, default: "" },
  required: { type: Boolean, default: false },
  inheritBadge: { type: String, default: "" }, // "inherit" | "override" | "new"
})
</script>
