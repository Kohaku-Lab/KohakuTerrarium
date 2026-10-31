<template>
  <component :is="wrapper" v-if="entries.length" :class="variant === 'badge' ? 'kt-graph-badges nodrag' : 'kt-graph-panels'" :data-test="`graph-${variant}s`">
    <div v-for="item in entries" :key="item.key" :class="variant === 'badge' ? 'kt-graph-badge' : 'kt-graph-panel'" :title="variant === 'badge' ? item.label : undefined" :data-test="`graph-${variant}-${item.id}`">
      <div v-if="variant === 'panel'" class="kt-graph-panel-title">{{ item.label }}</div>
      <component :is="item.component" />
    </div>
  </component>
</template>

<script setup>
import { computed, provide, readonly } from "vue"

import { badgesFor, graphNodeInfo, panelsFor } from "@/ext/graphSlots"
import { GRAPH_NODE_INJECT_KEY } from "@/ext/sdk/keys"

const props = defineProps({
  /** `badge` (on a canvas node) or `panel` (in the dock details). */
  variant: { type: String, required: true },
  /** Node kind: `creature`, `channel` or `group`. */
  kind: { type: String, required: true },
  /** The node's item from the graph model. */
  item: { type: Object, required: true },
})

const wrapper = computed(() => (props.variant === "badge" ? "span" : "section"))
const entries = computed(() => (props.variant === "badge" ? badgesFor(props.kind) : panelsFor(props.kind)))

provide(
  GRAPH_NODE_INJECT_KEY,
  computed(() => readonly(graphNodeInfo(props.kind, props.item))),
)
</script>

<style scoped>
.kt-graph-badges {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  min-width: 0;
  font-size: 10px;
}
.kt-graph-badge {
  display: inline-flex;
  max-width: 6rem;
  overflow: hidden;
}
.kt-graph-panels {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-top: 0.75rem;
}
.kt-graph-panel-title {
  margin-bottom: 0.25rem;
  font-size: 10px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  opacity: 0.6;
}
</style>
