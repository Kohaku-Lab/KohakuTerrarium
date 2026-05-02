<template>
  <button class="card-hover p-4 text-left flex flex-col items-start gap-1 hover:border-iolite hover:shadow-md transition-all min-h-24" @click="$emit('click')">
    <span :class="icon" class="text-xl text-iolite mb-1" />
    <span class="font-medium text-warm-800 dark:text-warm-200">{{ label }}</span>
    <span class="text-xs text-warm-500">{{ subtitle }}</span>
  </button>
</template>

<script setup>
defineProps({
  icon: { type: String, required: true },
  label: { type: String, required: true },
  subtitle: { type: String, default: "" },
})
defineEmits(["click"])
</script>
