<template>
  <div class="px-4 py-3 flex items-center gap-4" :class="inline ? '' : 'max-md:flex-col max-md:items-stretch max-md:gap-2'">
    <div class="flex-1 min-w-0">
      <div class="kt-text-body font-medium text-warm-800 dark:text-warm-100">{{ title }}</div>
      <div v-if="hint" class="kt-text-caption mt-0.5 text-warm-500">{{ hint }}</div>
      <slot name="detail" />
      <div v-if="status" class="kt-text-caption mt-1" :class="TONES[tone]" data-row-status>{{ status }}</div>
    </div>
    <div v-if="$slots.default" class="shrink-0 flex items-center gap-2 max-md:flex-wrap"><slot /></div>
  </div>
</template>

<script setup>
/**
 * One setting: title, hint and an optional status line on the left, its control on the right.
 * The control drops under the text on phones unless `inline` (a switch is small enough to stay).
 */
defineProps({
  title: { type: String, required: true },
  hint: { type: String, default: "" },
  status: { type: String, default: "" },
  tone: { type: String, default: "muted", validator: (v) => ["muted", "ok", "warn", "error"].includes(v) },
  inline: { type: Boolean, default: false },
})

const TONES = {
  muted: "text-warm-500",
  ok: "text-aquamarine-shadow dark:text-aquamarine-light",
  warn: "text-amber-shadow dark:text-amber-light",
  error: "text-coral",
}
</script>
