<template>
  <svg :width="width" :height="height" :viewBox="`0 0 ${width} ${height}`" preserveAspectRatio="none" class="overflow-visible">
    <!-- Filled area + stroke. Same iolite-with-low-opacity rhythm as
         the trace-tab histogram bars in the design language. -->
    <path v-if="path.area" :d="path.area" :fill="fillColor" :fill-opacity="fillOpacity" />
    <path v-if="path.stroke" :d="path.stroke" fill="none" :stroke="strokeColor" :stroke-width="strokeWidth" stroke-linejoin="round" stroke-linecap="round" />
    <!-- Last-bucket emphasis dot — gives the eye a "what's happening
         right now" anchor on flat sparklines. -->
    <circle v-if="lastPoint" :cx="lastPoint.x" :cy="lastPoint.y" :r="dotRadius" :fill="strokeColor" />
  </svg>
</template>

<script setup>
import { computed } from "vue"

const props = defineProps({
  /** Numeric series. Empty / single-element arrays render an empty SVG. */
  values: { type: Array, default: () => [] },
  width: { type: Number, default: 96 },
  height: { type: Number, default: 24 },
  /** SVG colours — defaults match the project's iolite gem token. */
  strokeColor: { type: String, default: "#5A4FCF" },
  fillColor: { type: String, default: "#5A4FCF" },
  fillOpacity: { type: Number, default: 0.18 },
  strokeWidth: { type: Number, default: 1.5 },
  dotRadius: { type: Number, default: 1.75 },
  /** When `true`, series shorter than `minPoints` is left-padded with
   *  zeros so a sparkline that just started filling renders a clean
   *  "rising from zero" curve rather than a single dot. */
  padToMin: { type: Boolean, default: true },
  minPoints: { type: Number, default: 12 },
})

const padded = computed(() => {
  const v = (props.values || []).map((x) => Number(x) || 0)
  if (!props.padToMin || v.length >= props.minPoints) return v
  const pad = new Array(props.minPoints - v.length).fill(0)
  return [...pad, ...v]
})

const path = computed(() => {
  const v = padded.value
  if (v.length === 0) return { stroke: "", area: "" }
  const w = props.width
  const h = props.height
  const max = Math.max(1, ...v)
  // Leave 1 px top/bottom inset so the stroke isn't clipped by the
  // SVG edge (preserveAspectRatio="none" stretches without padding).
  const inset = 1.5
  const span = h - inset * 2
  const stepX = v.length > 1 ? w / (v.length - 1) : 0
  const points = v.map((value, i) => {
    const x = i * stepX
    const y = inset + span - (value / max) * span
    return [x, y]
  })
  const stroke = points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`).join(" ")
  const last = points[points.length - 1]
  const first = points[0]
  const area = `${stroke} L${last[0].toFixed(2)} ${h.toFixed(2)} L${first[0].toFixed(2)} ${h.toFixed(2)} Z`
  return { stroke, area }
})

const lastPoint = computed(() => {
  const v = padded.value
  if (v.length === 0) return null
  const w = props.width
  const h = props.height
  const max = Math.max(1, ...v)
  const inset = 1.5
  const span = h - inset * 2
  const x = v.length > 1 ? w : 0
  const y = inset + span - (v[v.length - 1] / max) * span
  return { x, y }
})
</script>
