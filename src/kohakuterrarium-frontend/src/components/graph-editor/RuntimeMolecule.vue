<template>
  <div class="absolute select-none" :data-group-id="group.id" :style="positionStyle" @mousedown.stop="onMouseDown" @click.stop="onClick">
    <div class="relative h-full w-full rounded-3xl border-2 transition-all" :class="[selected ? 'border-iolite/70 bg-iolite/[0.06] dark:bg-iolite/[0.08] shadow-md shadow-iolite/10' : dropTarget ? 'border-iolite border-dashed bg-iolite/[0.10]' : 'border-warm-300/70 dark:border-warm-700/70 bg-warm-200/30 dark:bg-warm-900/40 hover:border-warm-400/60 dark:hover:border-warm-600/60']">
      <!-- Header strip -->
      <div class="absolute top-0 left-0 right-0 h-9 px-3 flex items-center justify-between rounded-t-3xl bg-warm-100/70 dark:bg-warm-900/60 border-b border-warm-300/40 dark:border-warm-700/40">
        <div class="flex items-center gap-2 min-w-0">
          <div class="i-carbon-chemistry text-warm-500 dark:text-warm-400 text-sm" />
          <span class="text-xs font-semibold text-warm-700 dark:text-warm-300 truncate">
            {{ group.label }}
          </span>
          <span class="text-[10px] text-warm-500 dark:text-warm-400 truncate"> · {{ memberCount }} obj · {{ relationCount }} rel </span>
        </div>
        <div class="flex items-center gap-1 shrink-0">
          <button class="w-6 h-6 flex items-center justify-center rounded text-warm-400 hover:text-iolite hover:bg-warm-200/60 dark:hover:bg-warm-700/50" :title="group.collapsed ? 'expand' : 'collapse'" @click.stop="$emit('toggle-collapse', group.id)">
            <div :class="group.collapsed ? 'i-carbon-chevron-right' : 'i-carbon-chevron-down'" class="text-xs" />
          </button>
          <button class="w-6 h-6 flex items-center justify-center rounded text-warm-400 hover:text-amber hover:bg-warm-200/60 dark:hover:bg-warm-700/50" title="Stop this session" @click.stop="$emit('dissolve', group.id)">
            <div class="i-carbon-cut text-xs" />
          </button>
        </div>
      </div>

      <!-- Drop overlay -->
      <div v-if="dropTarget" class="absolute inset-0 mt-9 rounded-3xl flex items-center justify-center pointer-events-none">
        <div class="px-3 py-1.5 rounded-lg bg-iolite/80 text-white text-xs font-medium shadow">drop to join {{ group.label }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

const props = defineProps({
  group: { type: Object, required: true },
  bounds: { type: Object, required: true },
  selected: { type: Boolean, default: false },
  dropTarget: { type: Boolean, default: false },
  memberCount: { type: Number, default: 0 },
  relationCount: { type: Number, default: 0 },
  zoom: { type: Number, default: 1 },
  // z-index for the membrane plane of this group's band. Provided
  // by the parent (GraphEditorTab) so it can keep all members of a
  // single group stacked together regardless of where another
  // group's nodes physically overlap.
  z: { type: Number, default: 10 },
})

const emit = defineEmits(["select", "drag", "drag-start", "drag-end", "toggle-collapse", "dissolve"])

const positionStyle = computed(() => ({
  left: props.bounds.x + "px",
  top: props.bounds.y + "px",
  width: props.bounds.width + "px",
  height: props.bounds.height + "px",
  zIndex: props.z,
}))

const dragging = ref(false)
let dragStart = null
let suppressClick = false

function onMouseDown(e) {
  if (e.button !== 0) return
  dragStart = { x: e.clientX, y: e.clientY, dx: 0, dy: 0 }
  suppressClick = false
  emit("drag-start", { id: props.group.id, event: e })
  const onMove = (ev) => {
    const dx = (ev.clientX - dragStart.x) / props.zoom
    const dy = (ev.clientY - dragStart.y) / props.zoom
    if (!dragging.value && Math.hypot(dx, dy) > 3) {
      dragging.value = true
      suppressClick = true
    }
    if (dragging.value) {
      const ddx = dx - dragStart.dx
      const ddy = dy - dragStart.dy
      dragStart.dx = dx
      dragStart.dy = dy
      emit("drag", { id: props.group.id, dx: ddx, dy: ddy })
    }
  }
  const onUp = (ev) => {
    window.removeEventListener("mousemove", onMove)
    window.removeEventListener("mouseup", onUp)
    if (dragging.value) emit("drag-end", { id: props.group.id, clientX: ev.clientX, clientY: ev.clientY })
    dragging.value = false
    dragStart = null
  }
  window.addEventListener("mousemove", onMove)
  window.addEventListener("mouseup", onUp)
}

function onClick() {
  if (suppressClick) return
  emit("select", props.group.id)
}
</script>
