<template>
  <div v-if="items.length" class="kt-status-items" :data-side="side" data-test="status-items">
    <div v-for="item in items" :key="item.key" class="kt-status-item" :style="{ maxWidth: `${statusItemWidth(item)}px` }" :title="item.label" :data-test="`status-item-${item.id}`">
      <component :is="item.component" />
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { statusItemsFor, statusItemWidth } from "@/ext/statusItems"

const props = defineProps({
  side: { type: String, default: "right" },
})

const items = computed(() => statusItemsFor(props.side))
</script>

<style scoped>
.kt-status-items {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
}
.kt-status-item {
  display: flex;
  align-items: center;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  user-select: none;
}
</style>
