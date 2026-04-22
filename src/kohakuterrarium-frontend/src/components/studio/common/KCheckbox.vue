<template>
  <label class="inline-flex items-center gap-2 cursor-pointer select-none">
    <input type="checkbox" :checked="modelValue" :disabled="disabled" class="w-3.5 h-3.5 rounded border-warm-300 dark:border-warm-600 text-iolite focus:ring-1 focus:ring-iolite cursor-pointer" @change="$emit('update:modelValue', $event.target.checked)" />
    <span v-if="label || $slots.default" class="text-sm text-warm-700 dark:text-warm-300">
      <slot>{{ label }}</slot>
    </span>
  </label>
</template>

<script setup>
defineProps({
  modelValue: { type: Boolean, default: false },
  label: { type: String, default: "" },
  disabled: { type: Boolean, default: false },
})

defineEmits(["update:modelValue"])
</script>
