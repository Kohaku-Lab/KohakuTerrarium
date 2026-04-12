<template>
  <span class="inline-block w-2 h-2 rounded-full" :class="dotClass" :title="status" />
</template>

<script setup>
const props = defineProps({
  status: { type: String, default: "idle" },
})

const dotClass = computed(() => {
  switch (props.status) {
    case "running":
    case "processing":
      return "bg-aquamarine kohaku-glow"
    case "idle":
    case "done":
      return "bg-amber"
    case "error":
      return "bg-coral"
    case "stopped":
      return "bg-warm-400"
    default:
      return "bg-warm-400"
  }
})
</script>
