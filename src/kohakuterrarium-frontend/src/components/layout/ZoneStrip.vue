<template>
  <Zone
    :zone-id="zoneId"
    :instance-id="instanceId"
    :show-empty="showEmpty"
    class="zone--strip bg-white dark:bg-warm-800 border-t border-warm-200 dark:border-warm-700 text-[11px]"
  />
</template>

<script setup>
import Zone from "./Zone.vue"

defineProps({
  zoneId: { type: String, default: "status-bar" },
  instanceId: { type: String, default: null },
  showEmpty: { type: Boolean, default: false },
})
</script>
