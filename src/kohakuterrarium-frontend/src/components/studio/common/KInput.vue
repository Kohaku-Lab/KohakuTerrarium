<template>
  <input :value="modelValue" :type="type" :placeholder="placeholder" :disabled="disabled" :class="['w-full px-2.5 py-1.5 rounded-md text-sm bg-warm-50 dark:bg-warm-950', 'border border-warm-200 dark:border-warm-700', 'text-warm-800 dark:text-warm-200 placeholder-warm-400 dark:placeholder-warm-600', 'focus:outline-none focus:border-iolite dark:focus:border-iolite-light', 'transition-colors', disabled && 'opacity-60 cursor-not-allowed']" @input="$emit('update:modelValue', $event.target.value)" />
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
