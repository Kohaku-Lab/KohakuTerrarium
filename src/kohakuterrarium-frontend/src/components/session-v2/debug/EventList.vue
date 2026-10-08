<template>
  <div ref="viewport" class="h-full overflow-y-auto font-mono text-[11px] outline-none" tabindex="0" data-test="v2-debug-list" @scroll="onScroll" @keydown="onKeydown">
    <div v-if="!rows.length" class="py-10 text-center text-warm-400 font-sans text-xs">{{ emptyLabel }}</div>
    <template v-else>
      <div :style="{ height: `${range.padTop}px` }" />
      <button v-for="(row, i) in shown" :key="row.key" type="button" class="w-full flex items-center gap-2 px-3 text-left border-b kt-v2-line" :class="row.key === selectedKey ? 'bg-iolite/10 text-warm-900 dark:text-warm-100' : 'hover:bg-warm-100 dark:hover:bg-warm-800/50'" :style="{ height: `${ROW_HEIGHT}px` }" :data-index="range.start + i" @click="$emit('select', row.key)">
        <span class="text-warm-400 shrink-0 w-20 truncate">{{ timeLabel(row.ts) }}</span>
        <span class="shrink-0 px-1.5 rounded max-w-24 truncate" :class="kindClass(row.kind)">{{ row.kind }}</span>
        <span v-if="row.tab || row.module || row.name" class="shrink-0 max-w-40 truncate text-iolite dark:text-iolite-light">{{ row.tab || row.module || row.name }}</span>
        <span class="truncate text-warm-700 dark:text-warm-300">{{ row.preview }}</span>
      </button>
      <div :style="{ height: `${range.padBottom}px` }" />
    </template>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue"

import { scrollTopFor, visibleRange } from "@/components/session-v2/model/debug/windowing"

/** Windowed list of debug rows: only the rows in view are mounted. `tail` keeps a list that grows downward pinned to its end. */
const props = defineProps({
  rows: { type: Array, required: true },
  selectedKey: { type: String, default: null },
  tail: { type: Boolean, default: false },
  emptyLabel: { type: String, default: "" },
})
const emit = defineEmits(["select"])

const ROW_HEIGHT = 28
const viewport = ref(null)
const scrollTop = ref(0)
const viewportHeight = ref(600)
let frame = null
let resizeObserver = null

const range = computed(() => visibleRange({ scrollTop: scrollTop.value, viewportHeight: viewportHeight.value, rowHeight: ROW_HEIGHT, count: props.rows.length }))
const shown = computed(() => props.rows.slice(range.value.start, range.value.end))

function measure() {
  const el = viewport.value
  if (!el) return
  scrollTop.value = el.scrollTop
  viewportHeight.value = el.clientHeight
}

function onScroll() {
  if (frame !== null) return
  frame = requestAnimationFrame(() => {
    frame = null
    measure()
  })
}

function nearEnd() {
  const el = viewport.value
  return !!el && el.scrollHeight - el.scrollTop - el.clientHeight < ROW_HEIGHT * 4
}

// Follow by the last row's key: a capped buffer keeps its length while its tail moves.
watch(
  () => props.rows[props.rows.length - 1]?.key,
  (next, prev) => {
    if (!props.tail || next === prev) return
    const pinned = nearEnd()
    nextTick(() => {
      const el = viewport.value
      if (el && pinned) {
        el.scrollTop = el.scrollHeight
        measure()
      }
    })
  },
)

function onKeydown(e) {
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return
  e.preventDefault()
  const idx = props.rows.findIndex((r) => r.key === props.selectedKey)
  const next = Math.max(0, Math.min(props.rows.length - 1, idx < 0 ? 0 : idx + (e.key === "ArrowDown" ? 1 : -1)))
  const row = props.rows[next]
  if (!row) return
  emit("select", row.key)
  const top = scrollTopFor(next, { scrollTop: scrollTop.value, viewportHeight: viewportHeight.value, rowHeight: ROW_HEIGHT })
  if (top != null && viewport.value) {
    viewport.value.scrollTop = top
    measure()
  }
}

function timeLabel(ts) {
  if (!ts) return "—"
  if (typeof ts === "string" && !/^\d{4}-/.test(ts)) return ts
  const d = new Date(ts)
  return Number.isNaN(d.getTime()) ? String(ts) : d.toLocaleTimeString()
}

const KIND_CLASS = {
  user: "bg-iolite/10 text-iolite",
  assistant: "bg-aquamarine/10 text-aquamarine",
  channel: "bg-taaffeite/10 text-taaffeite",
  error: "bg-coral/10 text-coral",
  warning: "bg-amber/10 text-amber",
  info: "bg-aquamarine/10 text-aquamarine",
  debug: "bg-warm-100 dark:bg-warm-800 text-warm-400",
  subagent: "bg-iolite/10 text-iolite",
  tool: "bg-warm-200/70 dark:bg-warm-800 text-warm-600 dark:text-warm-300",
}

function kindClass(kind) {
  return KIND_CLASS[kind] || "bg-warm-100 dark:bg-warm-800 text-warm-500"
}

onMounted(() => {
  measure()
  if (typeof ResizeObserver !== "undefined" && viewport.value) {
    resizeObserver = new ResizeObserver(measure)
    resizeObserver.observe(viewport.value)
  }
  if (props.tail && viewport.value) {
    viewport.value.scrollTop = viewport.value.scrollHeight
    measure()
  }
})
onBeforeUnmount(() => {
  if (frame !== null) cancelAnimationFrame(frame)
  resizeObserver?.disconnect()
})
</script>
