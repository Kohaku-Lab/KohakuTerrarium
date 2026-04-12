<template>
  <Zone
    :zone-id="zoneId"
    :instance-id="instanceId"
    :show-empty="showEmpty"
    class="zone--main bg-white dark:bg-warm-800"
  />
</template>

<script setup>
import Zone from "./Zone.vue"

defineProps({
  zoneId: { type: String, default: "main" },
  instanceId: { type: String, default: null },
  showEmpty: { type: Boolean, default: false },
})
</script>
