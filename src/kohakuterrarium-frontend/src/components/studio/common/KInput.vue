<template>
  <input :value="modelValue" :type="type" :placeholder="placeholder" :disabled="disabled" :class="['kt-v2-edge w-full px-2.5 py-1.5 rounded-lg text-sm bg-[var(--v2-card)]', 'border', 'text-warm-800 dark:text-warm-200 placeholder-warm-400 dark:placeholder-warm-600', 'focus:outline-none focus:border-iolite dark:focus:border-iolite-light', 'transition-colors', disabled && 'opacity-60 cursor-not-allowed']" @input="$emit('update:modelValue', $event.target.value)" />
</template>

<script setup>
defineProps({
  modelValue: { type: [String, Number], default: "" },
  type: { type: String, default: "text" },
  placeholder: { type: String, default: "" },
  disabled: { type: Boolean, default: false },
})

defineEmits(["update:modelValue"])
</script>
