<template>
  <div class="card p-3">
    <!-- Header: projection modes + domain readout -->
    <div class="flex items-center gap-2 text-[10px] text-warm-400 mb-1.5">
      <div class="flex items-center gap-0.5">
        <button v-for="m in TIMELINE_MODES" :key="m" class="px-1.5 py-0.5 rounded border font-mono" :class="m === mode ? 'border-iolite bg-iolite/10 text-iolite' : 'border-warm-300 dark:border-warm-700 text-warm-500 hover:text-warm-700'" :title="t(`sessionViewer.trace.timeline.modeHint.${m}`)" @click="setMode(m)">{{ t(`sessionViewer.trace.timeline.mode.${m}`) }}</button>
      </div>
      <span v-if="truncated" class="px-1 py-0 rounded bg-amber/15 text-amber font-mono" :title="t('sessionViewer.trace.timeline.truncatedHint')">{{ t("sessionViewer.trace.timeline.truncated") }}</span>
      <span class="ml-auto font-mono">
        <template v-if="range">{{ selectionLabel }}</template>
        <template v-else-if="model">{{ model.spans.length }} events</template>
      </span>
    </div>

    <div class="flex items-stretch gap-2">
      <!-- Lane labels -->
      <div class="flex flex-col justify-between py-0 text-[9px] text-warm-400 select-none shrink-0 w-10" aria-hidden="true">
        <span v-for="lane in TIMELINE_LANES" :key="lane" class="h-4 leading-4">{{ t(`sessionViewer.trace.timeline.lane.${lane}`) }}</span>
      </div>

      <!-- Track -->
      <div ref="trackEl" class="relative flex-1 min-w-0 rounded bg-warm-50 dark:bg-warm-800/40 overflow-hidden touch-none select-none" :style="{ height: `${TIMELINE_LANES.length * 18}px` }" :class="panning ? 'cursor-grabbing' : 'cursor-crosshair'" tabindex="0" :title="t('sessionViewer.trace.timeline.hint')" @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerEnd" @pointercancel="onPointerCancel" @pointerleave="onPointerLeave" @dblclick.prevent="resetAll" @contextmenu.prevent @keydown="onKeyDown">
        <div v-if="!model" class="absolute inset-0 flex items-center justify-center text-[11px] text-warm-400">{{ t("sessionViewer.trace.timeline.empty") }}</div>
        <template v-else>
          <!-- Lane separators -->
          <div v-for="i in TIMELINE_LANES.length - 1" :key="i" class="absolute inset-x-0 border-t border-warm-200/60 dark:border-warm-700/60 pointer-events-none" :style="{ top: `${i * 18}px` }" />

          <!-- Turn boundaries -->
          <div v-for="b in visibleBoundaries" :key="b.key" class="absolute top-0 bottom-0 border-l border-dashed border-warm-300 dark:border-warm-600 pointer-events-none" :style="{ left: `${b.x}%` }">
            <span v-if="showBoundaryLabels" class="absolute top-0 left-0.5 text-[8px] font-mono text-warm-400">#{{ b.label }}</span>
          </div>

          <!-- Span buckets -->
          <div v-for="b in buckets" :key="b.key" class="absolute rounded-[1px] hover:opacity-100" :class="bucketClass(b)" :style="bucketStyle(b)" :title="bucketTitle(b)" />

          <!-- Hover line moves independently so the span subtree keeps its computed styles. -->
          <div v-show="hovering && !dragStateActive" ref="hoverLineEl" data-testid="timeline-hover-line" class="absolute top-0 bottom-0 left-0 w-px bg-warm-400/60 pointer-events-none will-change-transform" />

          <!-- Selection overlay -->
          <div ref="selectionEl" data-testid="timeline-selection" class="absolute top-0 bottom-0 left-0 w-full origin-left bg-iolite/15 border-x border-iolite/60 pointer-events-none will-change-transform" style="display: none" />
        </template>
      </div>
    </div>

    <!-- Minimap: full-domain overview with viewport highlight -->
    <div v-if="model" class="flex items-stretch gap-2 mt-1.5">
      <div class="w-10 shrink-0" aria-hidden="true" />
      <div ref="minimapEl" class="relative flex-1 min-w-0 rounded bg-warm-50 dark:bg-warm-800/40 overflow-hidden touch-none select-none" :class="viewport ? 'cursor-pointer' : ''" :style="{ height: `${TIMELINE_LANES.length * 4}px` }" :title="t('sessionViewer.trace.timeline.minimapHint')" @pointerdown="onMinimapPointerDown" @pointermove="onMinimapPointerMove" @pointerup="onMinimapPointerUp" @pointercancel="minimapDragging = false">
        <div v-for="b in minimapBuckets" :key="b.key" class="absolute" :class="bucketClass(b)" :style="minimapBucketStyle(b)" />
        <!-- committed focus range, in full-domain coordinates -->
        <div v-if="rangeFractionFull" class="absolute top-0 bottom-0 bg-iolite/25 pointer-events-none" :style="{ left: `${rangeFractionFull.start}%`, width: `${rangeFractionFull.end - rangeFractionFull.start}%` }" />
        <!-- viewport window: dim outside, frame inside -->
        <template v-if="viewportFraction">
          <div class="absolute top-0 bottom-0 left-0 bg-warm-900/20 dark:bg-warm-100/10 pointer-events-none" :style="{ width: `${viewportFraction.start}%` }" />
          <div class="absolute top-0 bottom-0 right-0 bg-warm-900/20 dark:bg-warm-100/10 pointer-events-none" :style="{ left: `${viewportFraction.end}%` }" />
          <div class="absolute top-0 bottom-0 border border-iolite rounded-[2px] pointer-events-none" :style="{ left: `${viewportFraction.start}%`, width: `${Math.max(0.4, viewportFraction.end - viewportFraction.start)}%` }" />
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"

import { TIMELINE_LANES, TIMELINE_MODES, formatTimelineDuration, rasterizeTimelineSpans, rasterizeTurnBoundaries } from "@/components/sessions/trace/traceTimeline"
import { useI18n } from "@/utils/i18n"

const MIN_DRAG_PX = 3
const LANE_HEIGHT_PX = 18

const { t } = useI18n()

const props = defineProps({
  model: { type: Object, default: null },
  mode: { type: String, default: "sequence" },
  range: { type: Object, default: null },
  truncated: { type: Boolean, default: false },
})
const emit = defineEmits(["update:mode", "update:range", "select-span"])

const trackEl = ref(null)
const hoverLineEl = ref(null)
const selectionEl = ref(null)
const minimapEl = ref(null)
const trackWidth = ref(0)
const viewport = ref(null)
const hovering = ref(false)
const dragStateActive = ref(false)
const panning = ref(false)
const minimapDragging = ref(false)

let dragState = null
let panState = null
let resizeObserver = null
let pointerFrame = 0
let pendingPointer = null
let wheelFrame = 0
let pendingWheel = []

onMounted(() => {
  if (!trackEl.value) return
  resizeObserver = new ResizeObserver((entries) => {
    trackWidth.value = entries[0]?.contentRect?.width || 0
  })
  resizeObserver.observe(trackEl.value)
  trackWidth.value = trackEl.value.getBoundingClientRect().width
  trackEl.value.addEventListener("wheel", onWheel, { passive: false })
  updateSelectionPreview(props.range)
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  trackEl.value?.removeEventListener("wheel", onWheel)
  if (pointerFrame) cancelAnimationFrame(pointerFrame)
  if (wheelFrame) cancelAnimationFrame(wheelFrame)
})

// The range belongs to the current projection's domain; switching the
// projection would silently reinterpret it, so clear it instead.
function setMode(m) {
  if (m === props.mode) return
  cancelPendingWheel()
  emit("update:mode", m)
  emit("update:range", null)
  viewport.value = null
}

// Reset the viewport when the underlying data is replaced.
watch(
  () => props.model,
  (model, prev) => {
    if (!model) {
      cancelPointerInteraction()
      cancelPendingWheel()
      viewport.value = null
      hovering.value = false
      clearSelectionPreview()
      return
    }
    if (viewport.value && (viewport.value.end < model.start || viewport.value.start > model.end)) {
      viewport.value = null
    }
    void prev
  },
)

const fullDuration = computed(() => Math.max(1, (props.model?.end ?? 0) - (props.model?.start ?? 0)))

const domain = computed(() => {
  const model = props.model
  if (!model) return null
  if (!viewport.value) return { start: model.start, end: model.end }
  const duration = Math.min(fullDuration.value, Math.max(1, viewport.value.end - viewport.value.start))
  const start = Math.min(Math.max(viewport.value.start, model.start), model.end - duration)
  return { start, end: start + duration }
})

const domainDuration = computed(() => Math.max(1, (domain.value?.end ?? 1) - (domain.value?.start ?? 0)))

const columns = computed(() => {
  const w = trackWidth.value
  if (!w) return 400
  return Math.max(120, Math.min(1500, Math.floor(w / 2)))
})

// Rasterize spans into pixel-column bars: one div per visible span
// (multi-column spans merge into a single wide bar), so the DOM size
// stays bounded and hover moves don't re-render thousands of nodes.
const buckets = computed(() => {
  const model = props.model
  const d = domain.value
  if (!model || !d) return []
  return rasterizeTimelineSpans(model.spans, d, columns.value)
})

const visibleBoundaries = computed(() => {
  const model = props.model
  const d = domain.value
  if (!model || !d) return []
  return rasterizeTurnBoundaries(model.turnBoundaries, d, columns.value)
})

// Minimap rasterizes the full domain at fixed low resolution, ignoring
// the zoom viewport, so the current window stays locatable in context.
const MINIMAP_COLUMNS = 240

const minimapBuckets = computed(() => {
  const model = props.model
  if (!model) return []
  return rasterizeTimelineSpans(model.spans, { start: model.start, end: model.end }, MINIMAP_COLUMNS)
})

function minimapBucketStyle(b) {
  return {
    left: `${(b.col / MINIMAP_COLUMNS) * 100}%`,
    width: `${(b.spanCols / MINIMAP_COLUMNS) * 100}%`,
    minWidth: "1px",
    top: `${b.lane * 4}px`,
    height: "3px",
  }
}

const viewportFraction = computed(() => {
  const model = props.model
  const d = domain.value
  if (!model || !d || !viewport.value) return null
  const full = Math.max(1, model.end - model.start)
  return {
    start: ((d.start - model.start) / full) * 100,
    end: ((d.end - model.start) / full) * 100,
  }
})

const rangeFractionFull = computed(() => {
  const model = props.model
  const r = props.range
  if (!model || !r) return null
  const full = Math.max(1, model.end - model.start)
  const start = Math.min(Math.max(((Math.min(r.start, r.end) - model.start) / full) * 100, 0), 100)
  const end = Math.min(Math.max(((Math.max(r.start, r.end) - model.start) / full) * 100, 0), 100)
  return { start, end }
})

function minimapRecenter(event) {
  const model = props.model
  const rect = minimapEl.value?.getBoundingClientRect()
  if (!model || !viewport.value || !rect || rect.width <= 0) return
  const fraction = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width))
  const full = model.end - model.start
  const center = model.start + fraction * full
  const dur = domainDuration.value
  const start = Math.min(Math.max(center - dur / 2, model.start), model.end - dur)
  viewport.value = { start, end: start + dur }
}

function onMinimapPointerDown(event) {
  if (!viewport.value) return
  minimapDragging.value = true
  minimapEl.value?.setPointerCapture?.(event.pointerId)
  minimapRecenter(event)
}

function onMinimapPointerMove(event) {
  if (minimapDragging.value) minimapRecenter(event)
}

function onMinimapPointerUp() {
  minimapDragging.value = false
}

const showBoundaryLabels = computed(() => visibleBoundaries.value.length <= 24)

function updateSelectionPreview(range) {
  const element = selectionEl.value
  const d = domain.value
  if (!element || !range || !d) {
    clearSelectionPreview()
    return
  }
  const duration = domainDuration.value
  const start = Math.min(Math.max((Math.min(range.start, range.end) - d.start) / duration, 0), 1)
  const end = Math.min(Math.max((Math.max(range.start, range.end) - d.start) / duration, 0), 1)
  element.style.display = ""
  element.style.opacity = dragStateActive.value ? "0.7" : ""
  element.style.transform = `translate3d(${start * 100}%, 0, 0) scaleX(${end - start})`
}

function clearSelectionPreview() {
  if (!selectionEl.value) return
  selectionEl.value.style.display = "none"
  selectionEl.value.style.transform = ""
  selectionEl.value.style.opacity = ""
}

watch(
  () => [props.range, domain.value],
  ([range]) => {
    if (!dragState) updateSelectionPreview(range)
  },
  { flush: "post", immediate: true },
)

const LANE_CLASSES = ["bg-sage/70", "bg-iolite/70", "bg-aquamarine/70", "bg-taaffeite/70"]

function bucketClass(b) {
  if (b.error) return "bg-coral/80"
  return LANE_CLASSES[b.lane] || "bg-warm-400/70"
}

function bucketStyle(b) {
  const cols = columns.value
  return {
    left: `${(b.col / cols) * 100}%`,
    width: `${(b.spanCols / cols) * 100}%`,
    minWidth: "2px",
    top: `${b.lane * LANE_HEIGHT_PX + 2}px`,
    height: `${LANE_HEIGHT_PX - 5}px`,
  }
}

function formatClock(ms) {
  try {
    return new Date(ms).toLocaleTimeString(undefined, { hour12: false })
  } catch {
    return ""
  }
}

function bucketTitle(b) {
  const parts = []
  if (props.mode === "sequence") {
    parts.push(`${b.count} event${b.count === 1 ? "" : "s"}`)
  } else {
    parts.push(`${b.count} event${b.count === 1 ? "" : "s"}`)
    parts.push(`${formatClock(b.minStart)} → ${formatClock(b.maxEnd)}`)
    if (b.count === 1) parts.push(formatTimelineDuration(b.maxEnd - b.minStart))
  }
  if (b.labels.length) parts.push(b.labels.slice(0, 5).join(", "))
  else if (b.types.length) parts.push(b.types.slice(0, 5).join(", "))
  if (b.turns.length) parts.push(`turn ${b.turns.slice(0, 5).join(", ")}`)
  return parts.join("\n")
}

const selectionLabel = computed(() => {
  const r = props.range
  if (!r) return ""
  const width = Math.abs(r.end - r.start)
  if (props.mode === "sequence") return `${Math.max(1, Math.round(width))} events`
  return formatTimelineDuration(width)
})

function fractionAt(event, rect = trackEl.value?.getBoundingClientRect()) {
  if (!rect || rect.width <= 0) return 0
  return Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width))
}

function timeAt(fraction) {
  const d = domain.value
  if (!d) return 0
  return d.start + fraction * domainDuration.value
}

function laneAt(event) {
  const rect = trackEl.value?.getBoundingClientRect()
  if (!rect || rect.height <= 0) return null
  return Math.max(0, Math.min(TIMELINE_LANES.length - 1, Math.floor((event.clientY - rect.top) / LANE_HEIGHT_PX)))
}

function spanAtPoint(event) {
  const model = props.model
  if (!model) return null
  const t2 = timeAt(fractionAt(event))
  const lane = laneAt(event)
  // Snap tolerance of half a pixel column: point spans (zero-width, e.g.
  // in the time projection) would otherwise be unclickable.
  const tolerance = domainDuration.value / columns.value / 2
  const overlapping = model.spans.filter((s) => t2 + tolerance >= s.start && t2 - tolerance <= s.end)
  if (!overlapping.length) return null
  const inLane = overlapping.filter((s) => s.lane === lane)
  const candidates = inLane.length ? inLane : overlapping
  return candidates.reduce((best, s) => (Math.abs((s.start + s.end) / 2 - t2) < Math.abs((best.start + best.end) / 2 - t2) ? s : best))
}

function onPointerDown(event) {
  if (!props.model) return
  if (event.button === 2) {
    panState = { anchorClientX: event.clientX, anchorStart: domain.value.start, moved: false }
    panning.value = true
    trackEl.value?.setPointerCapture?.(event.pointerId)
    return
  }
  if (event.button !== 0) return
  const t2 = timeAt(fractionAt(event))
  dragState = { anchorTime: t2, anchorClientX: event.clientX }
  dragStateActive.value = true
  updateSelectionPreview({ start: t2, end: t2 })
  trackEl.value?.setPointerCapture?.(event.pointerId)
}

function onPointerMove(event) {
  pendingPointer = { clientX: event.clientX, clientY: event.clientY }
  if (!pointerFrame) pointerFrame = requestAnimationFrame(flushPointerMove)
}

function flushPointerMove() {
  if (pointerFrame) cancelAnimationFrame(pointerFrame)
  pointerFrame = 0
  const event = pendingPointer
  pendingPointer = null
  if (!event) return
  const rect = trackEl.value?.getBoundingClientRect()
  if (rect && rect.width > 0 && hoverLineEl.value) {
    const x = Math.min(Math.max(event.clientX - rect.left, 0), rect.width)
    hoverLineEl.value.style.transform = `translate3d(${x}px, 0, 0)`
    if (!hovering.value) hovering.value = true
  }
  if (!panState && !dragState) return
  if (panState) {
    if (Math.abs(event.clientX - panState.anchorClientX) >= MIN_DRAG_PX) panState.moved = true
    if (!viewport.value || !props.model || !rect || rect.width <= 0) return
    const delta = (event.clientX - panState.anchorClientX) / rect.width
    const dur = domainDuration.value
    const nextStart = Math.min(Math.max(panState.anchorStart - delta * dur, props.model.start), props.model.end - dur)
    viewport.value = { start: nextStart, end: nextStart + dur }
    return
  }
  const t2 = timeAt(fractionAt(event, rect))
  updateSelectionPreview({ start: dragState.anchorTime, end: t2 })
}

function onPointerEnd(event) {
  if (panState) {
    if (pendingPointer) flushPointerMove()
    const moved = panState.moved || Math.abs(event.clientX - panState.anchorClientX) >= MIN_DRAG_PX
    if (pointerFrame) cancelAnimationFrame(pointerFrame)
    pointerFrame = 0
    panState = null
    panning.value = false
    pendingPointer = null
    // Right-click without a drag clears the focus interval.
    if (!moved) emit("update:range", null)
    return
  }
  if (!dragState) return
  const t2 = timeAt(fractionAt(event))
  const range = { start: Math.min(dragState.anchorTime, t2), end: Math.max(dragState.anchorTime, t2) }
  const isClick = Math.abs(event.clientX - dragState.anchorClientX) < MIN_DRAG_PX
  dragState = null
  dragStateActive.value = false
  pendingPointer = null
  if (isClick) {
    const span = spanAtPoint(event)
    if (span) {
      clearSelectionPreview()
      emit("update:range", null)
      emit("select-span", span)
      return
    }
    // Click on empty track clears an existing focus.
    if (props.range) emit("update:range", null)
    clearSelectionPreview()
    return
  }
  if (range.end > range.start) emit("update:range", range)
  else clearSelectionPreview()
}

function cancelPointerInteraction() {
  if (pointerFrame) cancelAnimationFrame(pointerFrame)
  pointerFrame = 0
  pendingPointer = null
  dragState = null
  panState = null
  dragStateActive.value = false
  panning.value = false
}

function onPointerCancel() {
  cancelPointerInteraction()
  updateSelectionPreview(props.range)
}

function onPointerLeave() {
  if (dragState || panState) return
  if (pointerFrame) cancelAnimationFrame(pointerFrame)
  pointerFrame = 0
  pendingPointer = null
  hovering.value = false
}

function onWheel(event) {
  if (!props.model) return
  event.preventDefault()
  pendingWheel.push({ clientX: event.clientX, deltaY: event.deltaY })
  if (!wheelFrame) wheelFrame = requestAnimationFrame(flushWheel)
}

function flushWheel() {
  if (wheelFrame) cancelAnimationFrame(wheelFrame)
  wheelFrame = 0
  const events = pendingWheel
  pendingWheel = []
  const model = props.model
  if (!events.length || !model) return
  const rect = trackEl.value?.getBoundingClientRect()
  if (!rect || rect.width <= 0) return
  const minDuration = props.mode === "sequence" ? Math.min(4, fullDuration.value) : Math.min(20, fullDuration.value)
  let current = domain.value
  for (const event of events) {
    const fraction = fractionAt(event, rect)
    const duration = Math.max(1, current.end - current.start)
    const nextDuration = Math.min(fullDuration.value, Math.max(minDuration, duration * Math.exp(event.deltaY * 0.0015)))
    if (nextDuration >= fullDuration.value * 0.999) {
      current = { start: model.start, end: model.end }
      continue
    }
    const anchor = current.start + fraction * duration
    const nextStart = Math.min(Math.max(anchor - fraction * nextDuration, model.start), model.end - nextDuration)
    current = { start: nextStart, end: nextStart + nextDuration }
  }
  viewport.value = current.start === model.start && current.end === model.end ? null : current
}

function cancelPendingWheel() {
  pendingWheel = []
  if (wheelFrame) cancelAnimationFrame(wheelFrame)
  wheelFrame = 0
}

function resetAll() {
  cancelPendingWheel()
  viewport.value = null
  emit("update:range", null)
}

function onKeyDown(event) {
  if (event.key === "Escape" && props.range) {
    event.preventDefault()
    emit("update:range", null)
  }
}
</script>
