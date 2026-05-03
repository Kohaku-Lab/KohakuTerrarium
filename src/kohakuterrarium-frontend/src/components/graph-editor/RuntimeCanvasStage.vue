<template>
  <div ref="rootEl" class="relative h-full w-full overflow-hidden bg-warm-100 dark:bg-warm-950 select-none" :class="panning && 'cursor-grabbing'" @mousedown.self="onBgMouseDown" @click.self="$emit('background-click')" @wheel.prevent="onWheel">
    <!-- Subtle grid background -->
    <div class="absolute inset-0 pointer-events-none opacity-50" :style="gridStyle" />

    <!-- Pan/zoom transformed canvas -->
    <div class="absolute top-0 left-0 origin-top-left will-change-transform" :style="{ transform: `translate(${panX}px, ${panY}px) scale(${zoom})` }">
      <slot />
    </div>

    <!-- Top-left HUD -->
    <div class="absolute top-3 left-4 right-4 flex items-center justify-between pointer-events-none">
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

const props = defineProps({
  zoom: { type: Number, default: 1 },
  panX: { type: Number, default: 0 },
  panY: { type: Number, default: 0 },
  counts: { type: String, default: "" },
  log: { type: Array, default: () => [] },
})

const emit = defineEmits(["pan", "zoom", "zoom-at", "reset-view", "background-click", "background-mousedown"])

const rootEl = ref(null)
const panning = ref(false)

function onBgMouseDown(e) {
  if (e.button !== 0) return
  emit("background-mousedown", e)
  panning.value = true
  let lastX = e.clientX
  let lastY = e.clientY
  const onMove = (ev) => {
    emit("pan", { dx: ev.clientX - lastX, dy: ev.clientY - lastY })
    lastX = ev.clientX
    lastY = ev.clientY
  }
  const onUp = () => {
    panning.value = false
    window.removeEventListener("mousemove", onMove)
    window.removeEventListener("mouseup", onUp)
  }
  window.addEventListener("mousemove", onMove)
  window.addEventListener("mouseup", onUp)
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
