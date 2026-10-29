<template>
  <!-- kt.ui chart kinds with their own geometry: radar, heatmap, gauge. -->
  <figure class="ktui-chart" data-test="ktui-chart" :data-kind="kind">
    <figcaption v-if="title" class="ktui-section-title">{{ title }}</figcaption>
    <svg v-if="kind === 'radar'" class="ktui-radar" :viewBox="`0 0 ${RADAR} ${RADAR}`" :style="{ maxWidth: `${height}px` }" preserveAspectRatio="xMidYMid meet" role="img" :aria-label="title || 'radar chart'" data-test="ktui-radar">
      <polygon v-for="ring in RINGS" :key="ring" :points="ringPoints(ring)" class="ktui-chart-grid" />
      <line v-for="(axis, i) in axes" :key="`a${i}`" :x1="cx" :y1="cy" :x2="axis.x" :y2="axis.y" class="ktui-chart-grid" />
      <text v-for="(axis, i) in axes" :key="`t${i}`" :x="axis.lx" :y="axis.ly" class="ktui-chart-axis-label" :text-anchor="axis.anchor" dominant-baseline="middle">{{ axis.label }}</text>
      <polygon v-for="(s, si) in radarShapes" :key="si" :points="s" :fill="color(si)" fill-opacity="0.18" :stroke="color(si)" stroke-width="2" class="ktui-chart-mark" />
    </svg>
    <div v-else-if="kind === 'heatmap'" class="ktui-heatmap" :style="{ gridTemplateColumns: `max-content repeat(${columnCount}, minmax(1.75rem, 1fr))` }" role="table" :aria-label="title || 'heatmap'">
      <span />
      <span v-for="(label, i) in columnLabels" :key="`h${i}`" class="ktui-heatmap-head" role="columnheader">{{ toDisplay(label) }}</span>
      <template v-for="(row, r) in heatRows" :key="`r${r}`">
        <span class="ktui-heatmap-head ktui-heatmap-row" role="rowheader">{{ row.name || `row ${r + 1}` }}</span>
        <span v-for="(cell, c) in row.cells" :key="c" class="ktui-heatmap-cell" role="cell" :style="{ background: heatColor(cell) }" :title="cell === null ? '' : String(cell)">{{ cell === null ? "" : toDisplay(cell) }}</span>
      </template>
    </div>
    <svg v-else-if="kind === 'gauge'" :viewBox="`0 0 ${W} ${GAUGE_H}`" :height="Math.min(height, GAUGE_H)" width="100%" preserveAspectRatio="xMidYMid meet" role="img" :aria-label="`${gaugeName || title || 'gauge'}: ${gaugeValue}`">
      <path :d="arc(1)" class="ktui-gauge-track" />
      <path v-if="gaugeShare > 0" :d="arc(gaugeShare)" :stroke="color(0)" class="ktui-gauge-fill" />
      <text :x="W / 2" :y="GAUGE_H - 22" text-anchor="middle" class="ktui-gauge-value">{{ toDisplay(gaugeValue) }}</text>
      <text :x="W / 2" :y="GAUGE_H - 6" text-anchor="middle" class="ktui-chart-axis-label">{{ gaugeName }}</text>
      <text :x="W / 2 - GR" :y="GAUGE_H - 6" text-anchor="middle" class="ktui-chart-axis-label">{{ toDisplay(low) }}</text>
      <text :x="W / 2 + GR" :y="GAUGE_H - 6" text-anchor="middle" class="ktui-chart-axis-label">{{ toDisplay(high) }}</text>
    </svg>
    <div v-if="kind === 'radar' && series.length > 1" class="ktui-chart-legend">
      <span v-for="(s, i) in series" :key="i" class="ktui-chart-label"><i class="ktui-chart-swatch" :style="{ background: color(i) }" />{{ s.name || `series ${i + 1}` }}</span>
    </div>
  </figure>
</template>

<script setup>
import { computed } from "vue"

import { toDisplay } from "./expr.js"

const props = defineProps({
  kind: { type: String, required: true },
  labels: { type: Array, default: () => [] },
  series: { type: Array, default: () => [] },
  title: { type: String, default: "" },
  height: { type: Number, default: 240 },
  min: { type: Number, default: null },
  max: { type: Number, default: null },
})

const W = 400
const RINGS = [0.25, 0.5, 0.75, 1]
const GAUGE_H = 140
const GR = 110
const PALETTE = ["#5b8def", "#e8a33d", "#4cb782", "#d9534f", "#9b6dd6", "#3fb5c4"]

const color = (i) => PALETTE[i % PALETTE.length]
const finite = (v) => typeof v === "number" && Number.isFinite(v)
const numbers = (s) => (Array.isArray(s?.data) ? s.data.map((v) => (finite(v) ? v : null)) : [])

// radar: a square drawing that scales with its width, so it leaves no empty bands
const RADAR = 300
const cx = RADAR / 2
const cy = computed(() => RADAR / 2)
const radius = computed(() => RADAR / 2 - 44)
const axisCount = computed(() => Math.max(3, props.labels.length, ...props.series.map((s) => numbers(s).length)))
const radarMax = computed(() => (finite(props.max) ? props.max : Math.max(1, ...props.series.flatMap((s) => numbers(s).filter(finite)))))
const radarMin = computed(() => (finite(props.min) ? props.min : 0))

function polar(i, share) {
  const angle = -Math.PI / 2 + (i / axisCount.value) * Math.PI * 2
  return [cx + Math.cos(angle) * radius.value * share, cy.value + Math.sin(angle) * radius.value * share]
}

function ringPoints(share) {
  return Array.from({ length: axisCount.value }, (_, i) => polar(i, share).join(",")).join(" ")
}

const axes = computed(() =>
  Array.from({ length: axisCount.value }, (_, i) => {
    const [x, y] = polar(i, 1)
    const [lx, ly] = polar(i, 1.14)
    const anchor = Math.abs(lx - cx) < 4 ? "middle" : lx > cx ? "start" : "end"
    return { x, y, lx, ly, anchor, label: props.labels[i] !== undefined ? toDisplay(props.labels[i]) : "" }
  }),
)

const radarShapes = computed(() =>
  props.series.map((s) => {
    const span = radarMax.value - radarMin.value || 1
    return Array.from({ length: axisCount.value }, (_, i) => {
      const v = numbers(s)[i] ?? radarMin.value
      return polar(i, Math.min(1, Math.max(0, (v - radarMin.value) / span))).join(",")
    }).join(" ")
  }),
)

// heatmap
const heatRows = computed(() => props.series.map((s) => ({ name: toDisplay(s?.name ?? ""), cells: numbers(s) })))
const columnCount = computed(() => Math.max(props.labels.length, ...heatRows.value.map((r) => r.cells.length), 1))
const columnLabels = computed(() => Array.from({ length: columnCount.value }, (_, i) => props.labels[i] ?? ""))
const heatValues = computed(() => heatRows.value.flatMap((r) => r.cells.filter(finite)))
const heatLow = computed(() => (finite(props.min) ? props.min : Math.min(0, ...heatValues.value)))
const heatHigh = computed(() => (finite(props.max) ? props.max : Math.max(heatLow.value + 1, ...heatValues.value)))

function heatColor(v) {
  if (!finite(v)) return "transparent"
  const share = Math.min(1, Math.max(0, (v - heatLow.value) / (heatHigh.value - heatLow.value || 1)))
  return `rgba(91, 141, 239, ${(0.08 + share * 0.82).toFixed(3)})`
}

// gauge
const low = computed(() => (finite(props.min) ? props.min : 0))
const high = computed(() => (finite(props.max) ? props.max : 100))
const gaugeValue = computed(() => numbers(props.series[0])[0] ?? null)
const gaugeName = computed(() => toDisplay(props.series[0]?.name ?? ""))
const gaugeShare = computed(() => {
  if (!finite(gaugeValue.value)) return 0
  return Math.min(1, Math.max(0, (gaugeValue.value - low.value) / (high.value - low.value || 1)))
})

function arc(share) {
  const baseY = GAUGE_H - 30
  const start = [W / 2 - GR, baseY]
  const angle = Math.PI * (1 - share)
  const end = [W / 2 + Math.cos(angle) * GR, baseY - Math.sin(angle) * GR]
  return `M${start[0]},${start[1]} A${GR},${GR} 0 0,1 ${end[0].toFixed(2)},${end[1].toFixed(2)}`
}
</script>
