<template>
  <div class="relative">
    <select :value="modelValue" :disabled="disabled" :class="['w-full px-2.5 py-1.5 pr-8 rounded-md text-sm appearance-none', 'bg-warm-50 dark:bg-warm-950', 'border border-warm-200 dark:border-warm-700', 'text-warm-800 dark:text-warm-200', 'focus:outline-none focus:border-iolite dark:focus:border-iolite-light', 'transition-colors', disabled && 'opacity-60 cursor-not-allowed']" @change="$emit('update:modelValue', $event.target.value)">
      <option v-if="placeholder" value="" disabled>{{ placeholder }}</option>
      <option v-for="opt in normalizedOptions" :key="opt.value" :value="opt.value">
        {{ opt.label }}
      </option>
    </select>
    <div class="i-carbon-chevron-down absolute right-2 top-1/2 -translate-y-1/2 text-warm-500 pointer-events-none text-sm" />
  </div>
</template>

<script setup>
import { computed } from "vue"

const props = defineProps({
  modelValue: { type: [String, Number, null], default: "" },
  options: { type: Array, default: () => [] }, // ["a","b"] or [{value,label}]
  placeholder: { type: String, default: "" },
  disabled: { type: Boolean, default: false },
})

defineEmits(["update:modelValue"])

const normalizedOptions = computed(() => props.options.map((o) => (typeof o === "object" && o !== null ? o : { value: o, label: String(o) })))
</script>
