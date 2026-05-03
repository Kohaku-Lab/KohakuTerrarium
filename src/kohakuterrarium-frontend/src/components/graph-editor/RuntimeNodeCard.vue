<template>
  <div ref="root" class="absolute select-none transition-shadow" :class="[dragging && 'cursor-grabbing']" :style="positionStyle" :data-node-id="node.id" @mouseenter="hovered = true" @mouseleave="hovered = false" @mousedown.stop="onMouseDown" @click.stop="onClick" @contextmenu.prevent.stop="onContextMenu">
    <div class="border bg-warm-50 dark:bg-warm-900 transition-all" :class="[isChannel ? 'rounded-none' : 'rounded-xl', selected ? 'border-iolite ring-2 ring-iolite/40 shadow-lg shadow-iolite/10' : 'border-warm-300/70 dark:border-warm-700/70 hover:border-iolite/60 shadow-sm', dropTarget && 'ring-2 ring-iolite/60 border-iolite']" :style="{ width: width + 'px', height: height + 'px', clipPath: cardClipPath }">
      <div class="flex items-center gap-2 h-full" :class="isChannel ? 'px-5 py-2' : 'px-3 py-2'">
        <span class="relative flex shrink-0 items-center justify-center w-7 h-7" :class="[kindBg, isChannel ? 'rounded-full' : 'rounded-lg']">
          <span class="text-xs font-bold" :class="kindText">{{ kindGlyph }}</span>
          <span class="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border border-warm-50 dark:border-warm-900" :class="statusDotClass" />
        </span>
        <div class="min-w-0 flex-1">
          <div class="text-xs font-semibold text-warm-800 dark:text-warm-200 truncate">{{ node.label }}</div>
          <div class="text-[10px] text-warm-500 dark:text-warm-400 truncate">{{ node.kind }} · {{ node.status }}</div>
        </div>
      </div>
    </div>

    <!-- Connection handle: drag this knob to weave a wire to another
         node. Stays on the right edge so it never collides with the
         action chip below. Always visible (low-opacity at rest, full on
         hover) so users can discover the gesture. The decoration's
         z-index sits at the very top of THIS group's band so it
         doesn't poke up through other groups. -->
    <div class="absolute -right-2.5 top-1/2 -translate-y-1/2 w-5 h-5 rounded-full border-2 border-warm-50 dark:border-warm-900 shadow flex items-center justify-center cursor-crosshair transition-all" :style="{ zIndex: zDecoration }" :class="[hovered || selected ? 'bg-iolite scale-110' : 'bg-warm-300 dark:bg-warm-700 opacity-70 hover:opacity-100']" title="Drag to connect to another node" @mousedown.stop="onConnectHandleDown" @click.stop>
      <div class="i-carbon-connect text-[10px] text-warm-50" />
    </div>

    <!-- Halo / action chip — visible on hover or when selected. Single
         pill containing all actions with a clear background and a
         shadow so it stands out against the canvas. -->
    <div v-if="selected || hovered" class="absolute -bottom-3.5 left-1/2 -translate-x-1/2 flex items-stretch rounded-lg overflow-hidden bg-warm-50 dark:bg-warm-900 border border-warm-300/80 dark:border-warm-700/80 shadow-md" :style="{ zIndex: zDecoration }">
      <button class="w-8 h-7 flex items-center justify-center text-warm-600 dark:text-warm-300 hover:text-iolite hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors" title="More options" @mousedown.stop @click.stop="$emit('open-menu', node.id, $event)">
        <div class="i-carbon-overflow-menu-vertical text-base" />
      </button>
      <div class="w-px bg-warm-200/80 dark:bg-warm-700/80" />
      <button class="w-8 h-7 flex items-center justify-center text-warm-600 dark:text-warm-300 hover:text-amber hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed" :disabled="!node.groupId" :title="node.groupId ? 'Split out of group' : 'Not in a group'" @mousedown.stop @click.stop="node.groupId && $emit('split', node.id)">
        <div class="i-carbon-cut text-base" />
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import { NODE_HEIGHT, NODE_WIDTH, STATUS_COLOR } from "./nodeStyle"

const props = defineProps({
  node: { type: Object, required: true },
  selected: { type: Boolean, default: false },
  dropTarget: { type: Boolean, default: false },
  zoom: { type: Number, default: 1 },
  // z-index for this node's body. Selected state uses ``zSelected``
  // and decorations (connect handle / action chip) sit at
  // ``zDecoration``, all within the same group's band so a node and
  // its chrome stay together when another group overlaps.
  z: { type: Number, default: 30 },
  zSelected: { type: Number, default: 40 },
  zDecoration: { type: Number, default: 50 },
})

const emit = defineEmits(["select", "drag-start", "drag", "drag-end", "open-menu", "split", "connect-start"])

const root = ref(null)
const dragging = ref(false)
const hovered = ref(false)
const width = NODE_WIDTH
const height = NODE_HEIGHT

const positionStyle = computed(() => ({
  left: props.node.x + "px",
  top: props.node.y + "px",
  zIndex: props.selected ? props.zSelected : props.z,
}))

const isChannel = computed(() => props.node.kind === "channel")
const cardClipPath = computed(() => (isChannel.value ? "polygon(14px 0, calc(100% - 14px) 0, 100% 50%, calc(100% - 14px) 100%, 14px 100%, 0 50%)" : "none"))

const kindGlyph = computed(() => {
  switch (props.node.kind) {
    case "creature":
      return "C"
    case "session":
      return "S"
    case "terrarium":
      return "T"
    case "channel":
      return "≋"
    default:
      return "?"
  }
})
const kindBg = computed(() => {
  switch (props.node.kind) {
    case "creature":
      return "bg-iolite/15 dark:bg-iolite/20"
    case "session":
      return "bg-taaffeite/15 dark:bg-taaffeite/20"
    case "terrarium":
      return "bg-amber/15 dark:bg-amber/20"
    case "channel":
      return "bg-aquamarine/20 dark:bg-aquamarine/25"
    default:
      return "bg-warm-200 dark:bg-warm-800"
  }
})
const kindText = computed(() => {
  switch (props.node.kind) {
    case "creature":
      return "text-iolite dark:text-iolite-light"
    case "session":
      return "text-taaffeite dark:text-taaffeite-light"
    case "terrarium":
      return "text-amber dark:text-amber-light"
    case "channel":
      return "text-aquamarine dark:text-aquamarine-light"
    default:
      return "text-warm-500"
  }
})
const statusDotClass = computed(() => {
  const c = STATUS_COLOR[props.node.status] ?? "warm-400"
  return `bg-${c}`
})

let dragStart = null
let suppressClick = false

function onMouseDown(e) {
  if (e.button !== 0) return
  dragStart = { x: e.clientX, y: e.clientY, dx: 0, dy: 0 }
  suppressClick = false
  emit("drag-start", { id: props.node.id, event: e })

  const onMove = (ev) => {
    const dx = (ev.clientX - dragStart.x) / props.zoom
    const dy = (ev.clientY - dragStart.y) / props.zoom
    if (!dragging.value && (Math.abs(dx) > 2 || Math.abs(dy) > 2)) {
      dragging.value = true
      suppressClick = true
    }
    if (dragging.value) {
      const ddx = dx - dragStart.dx
      const ddy = dy - dragStart.dy
      dragStart.dx = dx
      dragStart.dy = dy
      emit("drag", {
        id: props.node.id,
        dx: ddx,
        dy: ddy,
        clientX: ev.clientX,
        clientY: ev.clientY,
      })
    }
  }
  const onUp = (ev) => {
    window.removeEventListener("mousemove", onMove)
    window.removeEventListener("mouseup", onUp)
    if (dragging.value) {
      emit("drag-end", { id: props.node.id, clientX: ev.clientX, clientY: ev.clientY })
    }
    dragging.value = false
    dragStart = null
  }
  window.addEventListener("mousemove", onMove)
  window.addEventListener("mouseup", onUp)
}

function onConnectHandleDown(e) {
  if (e.button !== 0) return
  // Read the handle's actual screen rect at mousedown time and pass
  // its centre along. Lets the parent draw the ghost wire from the
  // exact handle position without doing any canvas-coord math (which
  // bit us repeatedly because of pan/zoom / scaled-SVG quirks).
  const rect = e.currentTarget?.getBoundingClientRect?.()
  const sourceX = rect ? rect.left + rect.width / 2 : e.clientX
  const sourceY = rect ? rect.top + rect.height / 2 : e.clientY
  emit("connect-start", {
    id: props.node.id,
    clientX: e.clientX,
    clientY: e.clientY,
    sourceX,
    sourceY,
  })
}

function onClick() {
  if (suppressClick) return
  emit("select", props.node.id)
}

function onContextMenu(e) {
  emit("open-menu", props.node.id, e)
}
</script>
