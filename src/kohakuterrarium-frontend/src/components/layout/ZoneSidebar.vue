<template>
  <Zone
    :zone-id="zoneId"
    :instance-id="instanceId"
    :show-empty="showEmpty"
    class="zone--sidebar bg-warm-50 dark:bg-warm-900"
  />
</template>

<script setup>
import Zone from "./Zone.vue"

defineProps({
  zoneId: { type: String, required: true },
  instanceId: { type: String, default: null },
  showEmpty: { type: Boolean, default: false },
})
</script>
