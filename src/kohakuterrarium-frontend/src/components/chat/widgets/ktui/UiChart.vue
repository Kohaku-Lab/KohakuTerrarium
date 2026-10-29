<template>
  <!-- Small SVG chart for kt.ui: bar, line, area, pie, scatter. -->
  <figure class="ktui-chart" data-test="ktui-chart" :data-kind="kind">
    <figcaption v-if="title" class="ktui-section-title">{{ title }}</figcaption>
    <svg :viewBox="`0 0 ${W} ${height}`" :height="height" width="100%" :preserveAspectRatio="kind === 'pie' ? 'xMidYMid meet' : 'none'" role="img" :aria-label="title || `${kind} chart`">
      <template v-if="kind === 'pie'">
        <path v-for="(slice, i) in pieSlices" :key="i" :d="slice.d" :fill="color(i)" class="ktui-chart-mark" />
      </template>
      <template v-else>
        <line :x1="PAD" :x2="W - PAD" :y1="yOf(0)" :y2="yOf(0)" class="ktui-chart-axis" />
        <template v-if="kind === 'bar'">
          <template v-for="(s, si) in numericSeries" :key="si">
            <rect v-for="(v, i) in s.data" :key="i" :x="barX(i, si)" :y="Math.min(yOf(v), yOf(0))" :width="barWidth" :height="Math.abs(yOf(v) - yOf(0))" :fill="color(si)" class="ktui-chart-mark" />
          </template>
        </template>
        <template v-else-if="kind === 'scatter'">
          <template v-for="(s, si) in pointSeries" :key="si">
            <circle v-for="(p, i) in s.points" :key="i" :cx="xOfValue(p[0])" :cy="yOf(p[1])" r="3" :fill="color(si)" class="ktui-chart-mark" />
          </template>
        </template>
        <template v-else>
          <template v-for="(s, si) in numericSeries" :key="si">
            <path v-if="kind === 'area'" :d="areaPath(s.data)" :fill="color(si)" fill-opacity="0.2" stroke="none" />
            <polyline :points="linePoints(s.data)" fill="none" :stroke="color(si)" stroke-width="2" class="ktui-chart-mark" />
          </template>
        </template>
      </template>
    </svg>
    <div v-if="kind !== 'scatter' && labels.length" class="ktui-chart-labels">
      <span v-for="(label, i) in labels" :key="i" class="ktui-chart-label"> <i v-if="kind === 'pie'" class="ktui-chart-swatch" :style="{ background: color(i) }" />{{ toDisplay(label) }} </span>
    </div>
    <div v-if="kind !== 'pie' && series.length > 1" class="ktui-chart-legend">
      <span v-for="(s, i) in series" :key="i" class="ktui-chart-label"><i class="ktui-chart-swatch" :style="{ background: color(i) }" />{{ s.name || `series ${i + 1}` }}</span>
    </div>
  </figure>
</template>

<script setup>
import { computed } from "vue"

import { toDisplay } from "./expr.js"

const props = defineProps({
  kind: { type: String, default: "bar" },
  labels: { type: Array, default: () => [] },
  series: { type: Array, default: () => [] },
  title: { type: String, default: "" },
  height: { type: Number, default: 200 },
})

const W = 400
const PAD = 8
const PALETTE = ["#5b8def", "#e8a33d", "#4cb782", "#d9534f", "#9b6dd6", "#3fb5c4"]

const color = (i) => PALETTE[i % PALETTE.length]
const finite = (v) => typeof v === "number" && Number.isFinite(v)

const numericSeries = computed(() => props.series.map((s) => ({ ...s, data: s.data.map((v) => (finite(v) ? v : 0)) })))
const pointSeries = computed(() =>
  props.series.map((s) => ({
    ...s,
    points: s.data.filter((p) => Array.isArray(p) && finite(p[0]) && finite(p[1])),
  })),
)
const count = computed(() => Math.max(props.labels.length, ...numericSeries.value.map((s) => s.data.length), 1))

const yValues = computed(() => (props.kind === "scatter" ? pointSeries.value.flatMap((s) => s.points.map((p) => p[1])) : numericSeries.value.flatMap((s) => s.data)))
const yMin = computed(() => Math.min(0, ...yValues.value))
const yMax = computed(() => {
  const max = Math.max(0, ...yValues.value)
  return max === yMin.value ? yMin.value + 1 : max
})
const xValues = computed(() => pointSeries.value.flatMap((s) => s.points.map((p) => p[0])))
const xMin = computed(() => (xValues.value.length ? Math.min(...xValues.value) : 0))
const xMax = computed(() => {
  const max = xValues.value.length ? Math.max(...xValues.value) : 1
  return max === xMin.value ? xMin.value + 1 : max
})

function yOf(v) {
  const span = yMax.value - yMin.value
  return props.height - PAD - ((v - yMin.value) / span) * (props.height - 2 * PAD)
}

function xOf(i) {
  if (count.value <= 1) return W / 2
  return PAD + (i / (count.value - 1)) * (W - 2 * PAD)
}

function xOfValue(x) {
  return PAD + ((x - xMin.value) / (xMax.value - xMin.value)) * (W - 2 * PAD)
}

const slot = computed(() => (W - 2 * PAD) / count.value)
const barWidth = computed(() => Math.max(1, (slot.value * 0.8) / Math.max(1, numericSeries.value.length)))

function barX(i, si) {
  return PAD + i * slot.value + slot.value * 0.1 + si * barWidth.value
}

function linePoints(data) {
  return data.map((v, i) => `${xOf(i)},${yOf(v)}`).join(" ")
}

function areaPath(data) {
  if (!data.length) return ""
  const top = data.map((v, i) => `L${xOf(i)},${yOf(v)}`).join(" ")
  return `M${xOf(0)},${yOf(0)} ${top} L${xOf(data.length - 1)},${yOf(0)} Z`
}

const pieSlices = computed(() => {
  const data = (numericSeries.value[0]?.data || []).map((v) => Math.max(0, v))
  const total = data.reduce((a, b) => a + b, 0)
  if (!total) return []
  const cx = W / 2
  const cy = props.height / 2
  const r = Math.min(cx, cy) - PAD
  let angle = -Math.PI / 2
  return data.map((v) => {
    const sweep = (v / total) * Math.PI * 2
    const start = angle
    angle += sweep
    if (sweep >= Math.PI * 2 - 1e-9) {
      return { d: `M${cx - r},${cy} a${r},${r} 0 1,0 ${2 * r},0 a${r},${r} 0 1,0 ${-2 * r},0` }
    }
    const large = sweep > Math.PI ? 1 : 0
    const x1 = cx + r * Math.cos(start)
    const y1 = cy + r * Math.sin(start)
    const x2 = cx + r * Math.cos(angle)
    const y2 = cy + r * Math.sin(angle)
    return { d: `M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${large},1 ${x2},${y2} Z` }
  })
})
</script>
