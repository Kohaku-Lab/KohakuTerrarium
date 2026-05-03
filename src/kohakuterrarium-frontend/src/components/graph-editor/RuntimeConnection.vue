<template>
  <!-- Single SVG line for the wire body — no arrow head, no marker.
       Two direction toggles live at the midpoint as their own widget. -->
  <svg class="absolute pointer-events-none" :style="lineOuterStyle" :width="bbox.w" :height="bbox.h">
    <line :x1="local.sx" :y1="local.sy" :x2="local.tx" :y2="local.ty" :stroke="strokeColor" :stroke-width="strokeWidth" stroke-linecap="round" class="transition-[stroke,stroke-width] duration-100" style="pointer-events: visiblePainted; cursor: pointer" @mouseenter="onHover(true)" @mouseleave="onHover(false)" @mousedown.stop="onMouseDown" @click.stop="onClick" />
  </svg>

  <!-- Mid-line direction toggles. Stacked vertically so each row spells
       out "<source-id> → <target-id>" — no orientation ambiguity even
       when the line is steep or the cards are flipped. The up/down
       chevrons are visual cues only; the explicit ids carry meaning. -->
  <div class="absolute pointer-events-none rounded-md overflow-hidden shadow-md border border-warm-200/70 dark:border-warm-700/60" :style="midOuterStyle">
    <button class="pointer-events-auto cursor-pointer flex items-center gap-1.5 pl-1.5 pr-2 py-0.5 text-[10px] font-medium select-none transition-colors w-full whitespace-nowrap" :class="connection.aToB ? toggleOnClass : toggleOffClass" :title="`${source.label} → ${target.label}: ${connection.aToB ? 'on' : 'off'}`" @mousedown.stop @click.stop="$emit('toggle', connection.id, 'aToB')">
      <span class="i-carbon-chevron-up text-[10px] opacity-70 shrink-0" />
      <span class="flex-1 text-left">
        {{ source.label }}
        <span class="opacity-60">→</span>
        {{ target.label }}
        <span v-if="annotation.ab" class="opacity-60">· {{ annotation.ab }}</span>
      </span>
      <span class="opacity-80 shrink-0">{{ connection.aToB ? "✓" : "·" }}</span>
    </button>
    <div class="h-px bg-warm-300/40 dark:bg-warm-700/40 pointer-events-none" />
    <button class="pointer-events-auto cursor-pointer flex items-center gap-1.5 pl-1.5 pr-2 py-0.5 text-[10px] font-medium select-none transition-colors w-full whitespace-nowrap" :class="connection.bToA ? toggleOnClass : toggleOffClass" :title="`${target.label} → ${source.label}: ${connection.bToA ? 'on' : 'off'}`" @mousedown.stop @click.stop="$emit('toggle', connection.id, 'bToA')">
      <span class="i-carbon-chevron-down text-[10px] opacity-70 shrink-0" />
      <span class="flex-1 text-left">
        {{ target.label }}
        <span class="opacity-60">→</span>
        {{ source.label }}
        <span v-if="annotation.ba" class="opacity-60">· {{ annotation.ba }}</span>
      </span>
      <span class="opacity-80 shrink-0">{{ connection.bToA ? "✓" : "·" }}</span>
    </button>
  </div>

  <!-- Expanded info card (always horizontal) -->
  <div v-if="expanded" class="absolute pointer-events-auto" :style="cardOuterStyle">
    <div class="rounded-xl bg-warm-900/95 backdrop-blur border border-warm-100/10 px-3 py-2 text-warm-50 text-[11px] shadow-lg max-w-[300px] leading-snug">
      <div class="font-semibold mb-0.5">{{ connection.label }}</div>
      <div class="opacity-80 mb-1">{{ connection.brief }}</div>
      <div v-if="connection.details" class="opacity-60 mb-2">{{ connection.details }}</div>
      <div class="flex flex-wrap gap-1 mb-1">
        <span class="px-1.5 py-0.5 rounded bg-warm-100/10">{{ source.label }}</span>
        <span class="px-1.5 py-0.5 rounded bg-warm-100/10">↔</span>
        <span class="px-1.5 py-0.5 rounded bg-warm-100/10">{{ target.label }}</span>
      </div>
      <div class="flex flex-wrap gap-1">
        <button class="px-2 py-0.5 rounded bg-warm-100/10 hover:bg-iolite/40 transition-colors" @click.stop="$emit('action', 'open', connection.id)">open</button>
        <button class="px-2 py-0.5 rounded bg-warm-100/10 hover:bg-iolite/40 transition-colors" @click.stop="$emit('action', 'retarget', connection.id)">retarget</button>
        <button class="px-2 py-0.5 rounded bg-warm-100/10 hover:bg-coral/40 transition-colors" @click.stop="$emit('action', 'remove', connection.id)">remove</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import { NODE_HEIGHT, NODE_WIDTH } from "./nodeStyle"

const props = defineProps({
  connection: { type: Object, required: true },
  source: { type: Object, required: true }, // node corresponding to connection.a
  target: { type: Object, required: true }, // node corresponding to connection.b
  selected: { type: Boolean, default: false },
  expanded: { type: Boolean, default: false },
  zoom: { type: Number, default: 1 },
  // z-index for the wire's own band (membrane / connection plane of
  // the owning group). The expanded card is bumped a few layers
  // above this within the same band.
  z: { type: Number, default: 25 },
  zExpanded: { type: Number, default: 35 },
})

const emit = defineEmits(["select", "hover", "drag", "toggle", "action"])

const hovered = ref(false)

// Endpoints (rectangular edge of each node + perpendicular offset) -----
function rectEdge(cx, cy, hw, hh, dirX, dirY) {
  const ax = Math.abs(dirX) || 1e-9
  const ay = Math.abs(dirY) || 1e-9
  const t = Math.min(hw / ax, hh / ay)
  return [cx + dirX * t, cy + dirY * t]
}

const endpoints = computed(() => {
  const sx0 = props.source.x + NODE_WIDTH / 2
  const sy0 = props.source.y + NODE_HEIGHT / 2
  const tx0 = props.target.x + NODE_WIDTH / 2
  const ty0 = props.target.y + NODE_HEIGHT / 2
  let dx = tx0 - sx0
  let dy = ty0 - sy0
  const len = Math.hypot(dx, dy) || 1
  dx /= len
  dy /= len
  const [esx, esy] = rectEdge(sx0, sy0, NODE_WIDTH / 2 + 4, NODE_HEIGHT / 2 + 4, dx, dy)
  const [etx, ety] = rectEdge(tx0, ty0, NODE_WIDTH / 2 + 4, NODE_HEIGHT / 2 + 4, -dx, -dy)
  const total = props.connection.routeOffset || 0
  const px = -dy * total
  const py = dx * total
  return { sx: esx + px, sy: esy + py, tx: etx + px, ty: ety + py }
})

const strokeWidth = computed(() => (props.selected || hovered.value || props.expanded ? 8 : 5))
const strokeColor = computed(() => {
  const big = props.selected || hovered.value || props.expanded
  // Channel-involved wires get an aquamarine tint; pure peer wires use iolite.
  const channelInvolved = props.source.kind === "channel" || props.target.kind === "channel"
  if (channelInvolved) return big ? "rgba(76,153,137,0.95)" : "rgba(76,153,137,0.7)"
  return big ? "rgba(90,79,207,0.95)" : "rgba(90,79,207,0.7)"
})

const bbox = computed(() => {
  const e = endpoints.value
  const sw = strokeWidth.value
  const pad = sw + 2
  const minX = Math.min(e.sx, e.tx) - pad
  const minY = Math.min(e.sy, e.ty) - pad
  const maxX = Math.max(e.sx, e.tx) + pad
  const maxY = Math.max(e.sy, e.ty) + pad
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY }
})

const local = computed(() => {
  const e = endpoints.value
  const b = bbox.value
  return {
    sx: e.sx - b.x,
    sy: e.sy - b.y,
    tx: e.tx - b.x,
    ty: e.ty - b.y,
  }
})

const midpoint = computed(() => {
  const e = endpoints.value
  return { x: (e.sx + e.tx) / 2, y: (e.sy + e.ty) / 2 }
})

const lineOuterStyle = computed(() => ({
  left: bbox.value.x + "px",
  top: bbox.value.y + "px",
  zIndex: props.z,
}))

const midOuterStyle = computed(() => ({
  left: midpoint.value.x + "px",
  top: midpoint.value.y + "px",
  transform: "translate(-50%, -50%)",
  zIndex: props.expanded ? props.zExpanded : props.z,
}))

const cardOuterStyle = computed(() => ({
  left: midpoint.value.x + "px",
  top: midpoint.value.y + 30 + "px",
  transform: "translate(-50%, 0)",
  zIndex: props.zExpanded,
}))

// Channel-aware annotation appended to each toggle row. Empty for
// pure peer-to-peer wires (the explicit "<id> → <id>" already says it).
const annotation = computed(() => {
  const sIsChan = props.source.kind === "channel"
  const tIsChan = props.target.kind === "channel"
  if (sIsChan && !tIsChan) {
    // source = channel, target = creature
    // aToB = channel → creature = "recv" from creature's view
    return { ab: "recv", ba: "send" }
  }
  if (!sIsChan && tIsChan) {
    return { ab: "send", ba: "recv" }
  }
  return { ab: "", ba: "" }
})

const toggleOnClass = computed(() => {
  const channelInvolved = props.source.kind === "channel" || props.target.kind === "channel"
  return channelInvolved ? "bg-aquamarine text-warm-50" : "bg-iolite text-warm-50"
})
const toggleOffClass = "bg-warm-100/95 dark:bg-warm-900/95 text-warm-500 dark:text-warm-400"

// Interaction ----------------------------------------------------------
let suppressClick = false

function onMouseDown(e) {
  if (e.button !== 0) return
  suppressClick = false
  const startX = e.clientX
  const startY = e.clientY
  let lastPerp = 0
  const onMove = (ev) => {
    const dxScreen = ev.clientX - startX
    const dyScreen = ev.clientY - startY
    if (!suppressClick && Math.hypot(dxScreen, dyScreen) > 3) suppressClick = true
    if (!suppressClick) return
    const ep = endpoints.value
    const lx = ep.tx - ep.sx
    const ly = ep.ty - ep.sy
    const len = Math.hypot(lx, ly) || 1
    const ux = lx / len
    const uy = ly / len
    const perp = (-uy * dxScreen + ux * dyScreen) / props.zoom
    const ddPerp = perp - lastPerp
    lastPerp = perp
    emit("drag", { id: props.connection.id, dPerp: ddPerp })
  }
  const onUp = () => {
    window.removeEventListener("mousemove", onMove)
    window.removeEventListener("mouseup", onUp)
  }
  window.addEventListener("mousemove", onMove)
  window.addEventListener("mouseup", onUp)
}

function onClick() {
  if (suppressClick) return
  emit("select", props.connection.id)
}

function onHover(v) {
  hovered.value = v
  emit("hover", v ? props.connection.id : null)
}
</script>
