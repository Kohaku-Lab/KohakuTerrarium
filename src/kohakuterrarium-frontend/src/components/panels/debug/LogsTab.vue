<template>
  <div class="h-full flex flex-col overflow-hidden">
    <!-- Filter row -->
    <div
      class="flex items-center gap-2 px-3 py-1 border-b border-warm-200 dark:border-warm-700 shrink-0 text-[10px]"
    >
      <span
        class="w-1.5 h-1.5 rounded-full shrink-0"
        :class="stream.connected ? 'bg-aquamarine kohaku-pulse' : 'bg-warm-400'"
      />
      <span class="text-warm-400 font-mono truncate max-w-64">
        {{ stream.meta?.path || (stream.connected ? "connected" : "connecting…") }}
      </span>
      <el-select v-model="level" placeholder="level" size="small" clearable style="width: 100px">
        <el-option label="debug" value="debug" />
        <el-option label="info" value="info" />
        <el-option label="warning" value="warning" />
        <el-option label="error" value="error" />
      </el-select>
      <el-input
        v-model="query"
        placeholder="filter text..."
        size="small"
        clearable
        style="flex: 1; max-width: 320px"
      />
      <button
        class="w-5 h-5 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300"
        title="Clear"
        @click="stream.clear()"
      >
        <div class="i-carbon-close-outline text-[12px]" />
      </button>
    </div>

    <!-- Log lines -->
    <div ref="scrollEl" class="flex-1 overflow-y-auto font-mono text-[10px] px-3 py-1">
      <div v-for="(line, i) in visible" :key="i" class="flex gap-2 items-start py-[1px]">
        <span class="text-warm-400 shrink-0">{{ line.ts }}</span>
        <span class="shrink-0 uppercase w-12" :class="levelColor(line.level)">{{
          line.level
        }}</span>
        <span class="text-iolite shrink-0 max-w-40 truncate">{{ line.module }}</span>
        <span class="text-warm-700 dark:text-warm-300 flex-1 break-all whitespace-pre-wrap">{{
          line.text
        }}</span>
      </div>
      <div v-if="visible.length === 0" class="text-warm-400 text-center py-6">No log lines yet</div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue"

import { useLogStream } from "@/composables/useLogStream"

defineProps({
  instance: { type: Object, default: null },
})

const stream = useLogStream()

const level = ref("")
const query = ref("")
const scrollEl = ref(null)

const visible = computed(() => {
  const q = query.value.trim().toLowerCase()
  return stream.lines.value.filter((l) => {
    if (level.value && l.level !== level.value) return false
    if (!q) return true
    return (
      l.text.toLowerCase().includes(q) ||
      l.module.toLowerCase().includes(q) ||
      l.level.toLowerCase().includes(q)
    )
  })
})

function levelColor(l) {
  return (
    {
      debug: "text-warm-400",
      info: "text-aquamarine",
      warning: "text-amber",
      error: "text-coral",
      unknown: "text-warm-400",
    }[l] || "text-warm-500"
  )
}

// Auto-scroll to bottom on new lines if already near the bottom.
watch(
  () => visible.value.length,
  () => {
    nextTick(() => {
      const el = scrollEl.value
      if (!el) return
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
      if (distanceFromBottom < 200) {
        el.scrollTop = el.scrollHeight
      }
    })
  },
)
</script>
