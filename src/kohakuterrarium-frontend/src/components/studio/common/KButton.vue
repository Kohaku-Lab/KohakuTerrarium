<template>
  <button :type="type" :disabled="disabled" :class="['inline-flex items-center justify-center gap-1.5 rounded-md font-medium whitespace-nowrap transition-colors select-none', sizeClasses, variantClasses, disabled && 'opacity-50 cursor-not-allowed', !disabled && 'cursor-pointer']" @click="$emit('click', $event)">
    <div v-if="icon" :class="[icon, 'shrink-0', iconSizeClass]" />
    <slot />
  </button>
</template>

<script setup>
import { computed } from "vue"

const props = defineProps({
  variant: {
    type: String,
    default: "secondary", // primary | secondary | ghost | danger
  },
  size: {
    type: String,
    default: "md", // sm | md
  },
  type: { type: String, default: "button" },
  disabled: { type: Boolean, default: false },
  icon: { type: String, default: "" },
})

defineEmits(["click"])

const sizeClasses = computed(() => (props.size === "sm" ? "px-2 py-1 text-xs h-7" : "px-3 py-1.5 text-sm h-8"))

const iconSizeClass = computed(() => (props.size === "sm" ? "text-sm" : "text-base"))

const variantClasses = computed(() => {
  switch (props.variant) {
    case "primary":
      return "bg-iolite text-white hover:bg-iolite-shadow border border-transparent"
    case "danger":
      return "bg-coral text-white hover:bg-coral-shadow border border-transparent"
    case "ghost":
      return "bg-transparent text-warm-600 dark:text-warm-300 hover:bg-warm-200/60 dark:hover:bg-warm-800/60 border border-transparent"
    case "secondary":
    default:
      return "bg-warm-100 dark:bg-warm-900 text-warm-800 dark:text-warm-200 hover:bg-warm-200 dark:hover:bg-warm-800 border border-warm-200 dark:border-warm-700"
  }
})
</script>
