<template>
  <Zone
    :zone-id="zoneId"
    :instance-id="instanceId"
    :show-empty="showEmpty"
    class="zone--drawer bg-warm-50 dark:bg-warm-900 border-t border-warm-200 dark:border-warm-700"
  />
</template>

<script setup>
import Zone from "./Zone.vue"

defineProps({
  zoneId: { type: String, default: "drawer" },
  instanceId: { type: String, default: null },
  showEmpty: { type: Boolean, default: false },
})
</script>
