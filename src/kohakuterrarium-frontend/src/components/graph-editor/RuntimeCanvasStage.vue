<template>
  <div ref="rootEl" class="relative h-full w-full overflow-hidden bg-warm-100 dark:bg-warm-950 select-none" :class="panning && 'cursor-grabbing'" style="touch-action: none" @pointerdown.self="onBgPointerDown" @click.self="$emit('background-click')" @wheel.prevent="onWheel">
    <!-- Subtle grid background -->
    <div class="absolute inset-0 pointer-events-none opacity-50" :style="gridStyle" />

    <!-- Pan/zoom transformed canvas -->
    <div class="absolute top-0 left-0 origin-top-left will-change-transform" :style="{ transform: `translate(${panX}px, ${panY}px) scale(${zoom})` }">
      <slot />
    </div>

    <!-- Top-left HUD: counts text only (regular density). On compact
         the counts are duplicated in the GraphEditorTab bottom strip
         so we drop them here to free room. -->
    <div v-if="!isCompact" class="absolute top-3 left-4 right-4 flex items-center justify-between pointer-events-none">
      <div class="flex items-center gap-2 pointer-events-auto">
        <span class="text-[11px] text-warm-500 dark:text-warm-400">{{ counts }}</span>
      </div>
      <div class="flex items-center gap-1 pointer-events-auto">
        <button class="w-7 h-7 rounded-md bg-warm-200/60 dark:bg-warm-800/60 hover:bg-warm-300/60 dark:hover:bg-warm-700/60 text-warm-600 dark:text-warm-300 flex items-center justify-center" title="Zoom out" @click="$emit('zoom', 1 / 1.15)">
          <div class="i-carbon-zoom-out text-sm" />
        </button>
        <span class="text-[11px] tabular-nums text-warm-500 dark:text-warm-400 min-w-[36px] text-center">{{ Math.round(zoom * 100) }}%</span>
        <button class="w-7 h-7 rounded-md bg-warm-200/60 dark:bg-warm-800/60 hover:bg-warm-300/60 dark:hover:bg-warm-700/60 text-warm-600 dark:text-warm-300 flex items-center justify-center" title="Zoom in" @click="$emit('zoom', 1.15)">
          <div class="i-carbon-zoom-in text-sm" />
        </button>
        <button class="ml-1 px-2 py-1 rounded-md bg-warm-200/60 dark:bg-warm-800/60 hover:bg-warm-300/60 dark:hover:bg-warm-700/60 text-[11px] text-warm-600 dark:text-warm-300" title="Reset view" @click="$emit('reset-view')">reset</button>
      </div>
    </div>

    <!-- Compact: floating zoom cluster bottom-right. Larger touch
         targets (32px) and stacked vertically so they sit above the
         system safe area without competing with the bottom status
         strip in the parent GraphEditorTab. -->
    <div v-else class="absolute bottom-3 right-3 flex flex-col gap-1.5 pointer-events-auto" style="margin-bottom: env(safe-area-inset-bottom, 0px)">
      <button class="w-8 h-8 rounded-md bg-warm-50/95 dark:bg-warm-800/95 border border-warm-200 dark:border-warm-700 shadow-sm text-warm-600 dark:text-warm-300 flex items-center justify-center" title="Zoom in" @click="$emit('zoom', 1.15)">
        <div class="i-carbon-zoom-in text-base" />
      </button>
      <div class="text-[10px] tabular-nums text-warm-500 dark:text-warm-400 text-center px-1 select-none">{{ Math.round(zoom * 100) }}%</div>
      <button class="w-8 h-8 rounded-md bg-warm-50/95 dark:bg-warm-800/95 border border-warm-200 dark:border-warm-700 shadow-sm text-warm-600 dark:text-warm-300 flex items-center justify-center" title="Zoom out" @click="$emit('zoom', 1 / 1.15)">
        <div class="i-carbon-zoom-out text-base" />
      </button>
      <button class="w-8 h-8 rounded-md bg-warm-50/95 dark:bg-warm-800/95 border border-warm-200 dark:border-warm-700 shadow-sm text-warm-600 dark:text-warm-300 flex items-center justify-center" title="Reset view" @click="$emit('reset-view')">
        <div class="i-carbon-center-circle text-base" />
      </button>
    </div>

    <!-- Top-right transient log -->
    <div class="absolute top-12 right-4 flex flex-col gap-1 pointer-events-none">
      <div v-for="entry in log.slice(0, 4)" :key="entry.id" class="text-[11px] px-2 py-1 rounded-md bg-warm-50/90 dark:bg-warm-900/90 border border-warm-200/60 dark:border-warm-700/60 text-warm-600 dark:text-warm-300 shadow-sm whitespace-nowrap">
        {{ entry.msg }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import { useDensity } from "@/composables/useDensity"

const props = defineProps({
  zoom: { type: Number, default: 1 },
  panX: { type: Number, default: 0 },
  panY: { type: Number, default: 0 },
  counts: { type: String, default: "" },
  log: { type: Array, default: () => [] },
})

const emit = defineEmits(["pan", "zoom", "zoom-at", "reset-view", "background-click", "background-mousedown"])

const { isCompact } = useDensity()
const rootEl = ref(null)
const panning = ref(false)

// Pointer-based pan + pinch-to-zoom. Uses pointer events so the
// same handler works for mouse, touch, and pen. While ≥2 pointers
// are down we switch to pinch mode (two-finger zoom + pan); a
// single pointer continues normal drag-to-pan.
//
// `touch-action: none` on the root element is required so the
// browser doesn't claim the touch sequence for native scrolling
// before our handlers see it.
const _activePointers = new Map() // pointerId → { x, y }
let _pinchInitialDistance = 0
let _pinchInitialZoom = 1
let _pinchCenter = { x: 0, y: 0 }
let _panLast = null

function _pinchDistance() {
  const pts = Array.from(_activePointers.values())
  if (pts.length < 2) return 0
  const dx = pts[0].x - pts[1].x
  const dy = pts[0].y - pts[1].y
  return Math.sqrt(dx * dx + dy * dy)
}

function _pinchMidpoint() {
  const pts = Array.from(_activePointers.values())
  if (pts.length < 2) return { x: 0, y: 0 }
  return { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 }
}

function onBgPointerDown(e) {
  // Mouse: only main button. Touch/pen: button is 0 by default so
  // this same check works.
  if (e.pointerType === "mouse" && e.button !== 0) return
  emit("background-mousedown", e)
  e.target.setPointerCapture?.(e.pointerId)

  _activePointers.set(e.pointerId, { x: e.clientX, y: e.clientY })

  if (_activePointers.size === 1) {
    panning.value = true
    _panLast = { x: e.clientX, y: e.clientY }
  } else if (_activePointers.size === 2) {
    panning.value = false
    _pinchInitialDistance = _pinchDistance()
    _pinchInitialZoom = props.zoom
    _pinchCenter = _pinchMidpoint()
  }

  const onMove = (ev) => {
    if (!_activePointers.has(ev.pointerId)) return
    _activePointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY })

    if (_activePointers.size >= 2) {
      // Pinch: zoom toward the midpoint between the two pointers.
      const distance = _pinchDistance()
      if (_pinchInitialDistance > 0 && distance > 0) {
        const desired = _pinchInitialZoom * (distance / _pinchInitialDistance)
        const factor = desired / props.zoom
        const rect = rootEl.value?.getBoundingClientRect()
        const mid = _pinchMidpoint()
        const ax = rect ? mid.x - rect.left : 0
        const ay = rect ? mid.y - rect.top : 0
        emit("zoom-at", { factor, ax, ay })
      }
    } else if (_activePointers.size === 1 && _panLast) {
      emit("pan", { dx: ev.clientX - _panLast.x, dy: ev.clientY - _panLast.y })
      _panLast = { x: ev.clientX, y: ev.clientY }
    }
  }

  const onUp = (ev) => {
    _activePointers.delete(ev.pointerId)
    if (_activePointers.size === 0) {
      panning.value = false
      _panLast = null
      window.removeEventListener("pointermove", onMove)
      window.removeEventListener("pointerup", onUp)
      window.removeEventListener("pointercancel", onUp)
    } else if (_activePointers.size === 1) {
      // Drop back from pinch into single-pointer pan; reseed lastXY
      // from whichever pointer remains so we don't jump.
      const remaining = Array.from(_activePointers.values())[0]
      _panLast = { x: remaining.x, y: remaining.y }
      panning.value = true
    }
  }

  window.addEventListener("pointermove", onMove)
  window.addEventListener("pointerup", onUp)
  window.addEventListener("pointercancel", onUp)
}

function onWheel(e) {
  const rect = rootEl.value?.getBoundingClientRect()
  const ax = rect ? e.clientX - rect.left : 0
  const ay = rect ? e.clientY - rect.top : 0
  const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1
  emit("zoom-at", { factor, ax, ay })
}

const gridStyle = computed(() => {
  const size = 32 * props.zoom
  const offX = props.panX % size
  const offY = props.panY % size
  return {
    backgroundImage: "radial-gradient(circle at 1px 1px, rgba(120,110,100,0.18) 1px, transparent 1px)",
    backgroundSize: `${size}px ${size}px`,
    backgroundPosition: `${offX}px ${offY}px`,
  }
})

defineExpose({ rootEl })
</script>
