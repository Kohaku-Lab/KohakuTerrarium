<template>
  <g :class="[data.dimmed ? 'kt-graph-edge--dim' : '', data.lit ? 'kt-graph-edge--lit' : '']">
    <BaseEdge :id="id" :path="geometry.path" :marker-end="markerEnd" :marker-start="markerStart" :style="strokeStyle" :interaction-width="16" />
    <path v-if="pulsing" :key="data.pulseAt" :d="geometry.path" class="kt-graph-pulse-edge" :style="{ stroke: color }" fill="none" />
    <EdgeLabelRenderer v-if="label">
      <div class="nodrag nopan absolute pointer-events-none px-1.5 rounded text-[10px] leading-4 font-mono whitespace-nowrap kt-graph-edge-label" :class="labelClass" :style="labelStyle">
        {{ label }}
      </div>
    </EdgeLabelRenderer>
  </g>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue"
import { BaseEdge, EdgeLabelRenderer } from "@vue-flow/core"

import { EDGE_COLOR } from "@/components/graph/graphTheme"
import { floatingPath, nodeBox } from "@/utils/graph/layout/route/floating"
import { roundedPath, routeMidpoint } from "@/utils/graph/layout/route/route"

const props = defineProps({
  id: { type: String, required: true },
  sourceNode: { type: Object, required: true },
  targetNode: { type: Object, required: true },
  markerEnd: { type: String, default: undefined },
  markerStart: { type: String, default: undefined },
  selected: { type: Boolean, default: false },
  data: { type: Object, default: () => ({}) },
})

const PULSE_MS = 900

const geometry = computed(() => {
  const points = props.data.points
  if (points?.length > 1) {
    const mid = props.data.labelPos || routeMidpoint(points)
    return { path: roundedPath(points), labelX: mid.x, labelY: mid.y }
  }
  return floatingPath(nodeBox(props.sourceNode), nodeBox(props.targetNode), props.data.bend || 0)
})

const color = computed(() => {
  if (props.selected) return EDGE_COLOR.focus
  if (props.data.control && props.data.kind === "channel") return EDGE_COLOR.control
  return EDGE_COLOR[props.data.kind] || EDGE_COLOR.channel
})

const strokeStyle = computed(() => {
  const kind = props.data.kind
  const emphasis = props.selected || props.data.lit
  const width = kind === "wire" ? 2.25 : 1.5
  const base = {
    stroke: color.value,
    strokeWidth: emphasis ? width + 1 : width,
    strokeOpacity: emphasis ? 1 : kind === "wire" ? 0.85 : 0.45,
  }
  // Channel links are always solid; dashes mean a ping wire (alone or folded into an arrow), dots lineage.
  if (kind === "lineage") return { ...base, strokeDasharray: "2 5", strokeWidth: 1.5 }
  const style = { ...base }
  if (kind === "authority") Object.assign(style, { strokeWidth: emphasis ? 2.5 : 1.5, strokeOpacity: emphasis ? 1 : 0.8 })
  if (props.data.control && kind === "channel" && !emphasis) style.strokeOpacity = 0.7
  if (props.data.back && !emphasis) style.strokeOpacity = 0.3
  if ((kind === "wire" && props.data.withContent === false) || props.data.ping) style.strokeDasharray = "8 5"
  if (props.data.count > 1) style.strokeWidth = Math.min(base.strokeWidth + props.data.count * 0.5, 6)
  return style
})

const label = computed(() => {
  const d = props.data
  if (d.label) return d.label
  if (d.count > 1) return `×${d.count}`
  if (!(props.selected || d.lit)) return ""
  if (d.kind === "wire") return d.prompt ? "wire · prompt" : d.withContent === false ? "wire · ping" : "wire"
  if (d.kind === "via") return d.labels?.join(", ") || d.channelName || ""
  return ""
})

const labelClass = computed(() => {
  if (props.selected) return "text-iolite"
  if (props.data.kind === "wire") return "text-sapphire dark:text-sapphire-light"
  if (props.data.control) return "text-iolite dark:text-iolite-light"
  return "text-aquamarine-shadow dark:text-aquamarine-light"
})

const labelStyle = computed(() => ({
  transform: `translate(-50%, -50%) translate(${geometry.value.labelX}px, ${geometry.value.labelY}px)`,
}))

const pulsing = ref(false)
let timer = null
watch(
  () => props.data.pulseAt,
  (at) => {
    if (!at || Date.now() - at > PULSE_MS) return
    pulsing.value = true
    clearTimeout(timer)
    timer = setTimeout(() => (pulsing.value = false), PULSE_MS)
  },
)
onBeforeUnmount(() => clearTimeout(timer))
</script>
