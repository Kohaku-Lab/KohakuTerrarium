<template>
  <pre class="bg-warm-100 dark:bg-warm-800 rounded p-2 text-[10px] text-warm-600 dark:text-warm-400 max-h-48 overflow-auto whitespace-pre-wrap">{{ text }}</pre>
</template>

<script setup>
import { computed } from "vue"

const props = defineProps({
  value: { type: [Object, Array, String, Number, Boolean], default: null },
})

const text = computed(() => {
  try {
    return JSON.stringify(props.value, null, 2)
  } catch {
    return String(props.value)
  }
})
</script>
