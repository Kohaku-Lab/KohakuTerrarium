<template>
  <div ref="root" class="relative h-full w-full overflow-hidden touch-none" :class="panning ? 'cursor-grabbing' : 'cursor-grab'" data-test="lab-canvas" @wheel.prevent="onWheel" @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="onPointerUp" @dblclick.self="fit">
    <div class="absolute left-0 top-0 origin-top-left" :style="{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`, width: `${layout.bounds.w}px`, height: `${layout.bounds.h}px` }">
      <div v-for="lane in layout.lanes" :key="lane.hostId" class="kt-lab-lane absolute rounded-2xl" :style="{ left: `${lane.x}px`, top: `${lane.y}px`, width: `${lane.w}px`, height: `${lane.h}px` }" :data-test="`lab-lane-${lane.hostId}`">
        <div class="h-[34px] px-4 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-warm-500"><span class="i-carbon-bare-metal-server" />{{ lane.hostId === HOST_SITE ? t("cluster.site.host") : lane.hostId }}</div>
      </div>
      <template v-for="item in layout.items" :key="item.kind === 'start' ? 'start' : item.tank.id">
        <button v-if="item.kind === 'start'" type="button" class="kt-lab-start absolute rounded-2xl border-2 border-dashed flex flex-col items-center justify-center gap-1.5 text-warm-500 hover:text-iolite hover:border-iolite/60" :style="{ left: `${item.x}px`, top: `${item.y}px`, width: `${item.w}px`, height: `${item.h}px` }" data-test="lab-start" @click="$emit('new')">
          <span class="i-carbon-add-large text-2xl" />
          <span class="text-[13px] font-medium">{{ t("lab.new.title") }}</span>
        </button>
        <LabTank v-else :item="item" :active="activeIds.has(item.tank.id)" :last-message="lastMessages[item.tank.id] || null" @focus="$emit('focus', $event)" @open="$emit('open', $event)" @menu="$emit('menu', $event)" />
      </template>
    </div>

    <div class="absolute right-3 bottom-3 flex flex-col gap-1" @pointerdown.stop>
      <button v-for="b in CONTROLS" :key="b.id" type="button" class="kt-v2-float kt-v2-edge w-8 h-8 rounded-lg border flex items-center justify-center text-warm-600 dark:text-warm-300 hover:text-iolite shadow-sm" :title="t(b.label)" :data-test="`lab-${b.id}`" @click="b.act()"><span :class="b.icon" /></button>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref, watch } from "vue"

import LabTank from "@/components/lab/LabTank.vue"
import { fitView } from "@/components/lab/model/labLayout"
import { HOST_SITE } from "@/utils/graph/data/model"
import { useI18n } from "@/utils/i18n"

/**
 * The bench canvas: drag to pan, wheel to zoom at the pointer, double-click
 * the background (or ⤢) to fit. It fits itself when the bench changes until
 * the user moves it. Emits `resize` with its width so the bench can wrap.
 */
const props = defineProps({
  layout: { type: Object, required: true },
  structure: { type: String, required: true },
  activeIds: { type: Set, default: () => new Set() },
  lastMessages: { type: Object, default: () => ({}) },
})
const emit = defineEmits(["focus", "open", "menu", "new", "resize"])

const MIN_K = 0.25
const MAX_K = 1.6
const { t } = useI18n()
const root = ref(null)
const view = reactive({ x: 0, y: 0, k: 1 })
const panning = ref(false)
let moved = false
let drag = null
let observer = null
let size = { w: 0, h: 0 }

function fit() {
  if (!size.w || !size.h) return
  Object.assign(view, fitView(props.layout.bounds, size.w, size.h))
  moved = false
}

function zoomBy(factor, px = size.w / 2, py = size.h / 2) {
  const k = Math.min(MAX_K, Math.max(MIN_K, view.k * factor))
  view.x = px - ((px - view.x) * k) / view.k
  view.y = py - ((py - view.y) * k) / view.k
  view.k = k
  moved = true
}

const CONTROLS = [
  { id: "zoom-in", label: "lab.canvas.zoomIn", icon: "i-carbon-zoom-in", act: () => zoomBy(1.2) },
  { id: "zoom-out", label: "lab.canvas.zoomOut", icon: "i-carbon-zoom-out", act: () => zoomBy(1 / 1.2) },
  { id: "fit", label: "lab.canvas.fit", icon: "i-carbon-fit-to-screen", act: fit },
]

function onWheel(e) {
  const rect = root.value.getBoundingClientRect()
  zoomBy(Math.exp(-e.deltaY * 0.0015), e.clientX - rect.left, e.clientY - rect.top)
}

// Only the bench background pans; tanks and buttons keep their clicks.
function onPointerDown(e) {
  if (e.button !== 0 || e.target.closest("button, [role='button']")) return
  drag = { id: e.pointerId, x: e.clientX, y: e.clientY, vx: view.x, vy: view.y }
  panning.value = true
  root.value.setPointerCapture?.(e.pointerId)
}
function onPointerMove(e) {
  if (!drag || e.pointerId !== drag.id) return
  view.x = drag.vx + e.clientX - drag.x
  view.y = drag.vy + e.clientY - drag.y
  moved = true
}
function onPointerUp(e) {
  if (!drag || e.pointerId !== drag.id) return
  drag = null
  panning.value = false
}

watch(
  () => props.structure,
  () => {
    if (!moved) fit()
  },
)

function measure(width, height) {
  // Hidden (zoomed into a session) reads as 0×0; keep the last real size.
  if (!width || !height) return
  size = { w: width, h: height }
  emit("resize", size.w)
  if (!moved) fit()
}

onMounted(() => {
  if (typeof ResizeObserver === "undefined") {
    measure(root.value.clientWidth, root.value.clientHeight)
    return
  }
  observer = new ResizeObserver(([entry]) => measure(entry.contentRect.width, entry.contentRect.height))
  observer.observe(root.value)
})
onBeforeUnmount(() => observer?.disconnect())

defineExpose({ fit })
</script>

<style scoped>
.kt-lab-lane {
  background: var(--v2-panel);
  box-shadow: inset 0 0 0 1px var(--v2-line);
}
.kt-lab-start {
  border-color: var(--v2-line-strong);
  background: transparent;
}
</style>
