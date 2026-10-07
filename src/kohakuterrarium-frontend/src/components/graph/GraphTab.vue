<template>
  <GraphSurface :store-key="tab.id" :session-id="tab.target || null" />
</template>

<script setup>
import GraphSurface from "@/components/graph/GraphSurface.vue"

defineProps({ tab: { type: Object, required: true } })
</script>
