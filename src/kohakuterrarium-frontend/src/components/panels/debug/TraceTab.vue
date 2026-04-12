<template>
  <div class="h-full flex flex-col overflow-hidden">
    <div
      class="px-3 py-1 border-b border-warm-200 dark:border-warm-700 shrink-0 text-[10px] text-warm-400"
    >
      Tool call timings for the current tab. Bars scale to the widest span.
    </div>
    <div class="flex-1 overflow-y-auto px-3 py-2 text-[10px]">
      <div v-if="spans.length === 0" class="text-warm-400 text-center py-6">
        No tool calls in this tab yet.
      </div>
      <div v-else class="flex flex-col gap-0.5">
        <div v-for="(s, i) in spans" :key="i" class="flex items-center gap-2">
          <span class="font-mono text-iolite shrink-0 w-24 truncate">{{ s.name }}</span>
          <div class="flex-1 relative h-3 bg-warm-100 dark:bg-warm-800 rounded">
            <div
              class="absolute top-0 h-full rounded"
              :class="s.errored ? 'bg-coral' : 'bg-aquamarine'"
              :style="{
                left: s.offsetPct + '%',
                width: Math.max(s.widthPct, 1) + '%',
              }"
              :title="`${s.duration}ms · ${s.name}`"
            />
          </div>
          <span class="text-warm-400 font-mono shrink-0 w-14 text-right"> {{ s.duration }}ms </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useChatStore } from "@/stores/chat"

defineProps({
  instance: { type: Object, default: null },
})

const chat = useChatStore()

const spans = computed(() => {
  const tab = chat.activeTab
  if (!tab) return []
  const msgs = chat.messagesByTab?.[tab] || []
  const raw = []
  for (const m of msgs) {
    const tcs = m.tool_calls
    if (!tcs) continue
    for (const tc of tcs) {
      if (!tc?.startedAt && !tc?.started_at) continue
      const start = tc.startedAt || tc.started_at
      const end = tc.endedAt || tc.ended_at || tc.completedAt
      const startMs = typeof start === "number" ? start : Date.parse(start)
      const endMs = end ? (typeof end === "number" ? end : Date.parse(end)) : startMs
      if (!Number.isFinite(startMs)) continue
      raw.push({
        name: tc.name,
        start: startMs,
        end: endMs,
        duration: Math.max(0, endMs - startMs),
        errored: tc.status === "error",
      })
    }
  }
  if (raw.length === 0) return []
  const minStart = Math.min(...raw.map((r) => r.start))
  const maxEnd = Math.max(...raw.map((r) => r.end))
  const totalMs = Math.max(1, maxEnd - minStart)
  return raw.map((r) => ({
    ...r,
    offsetPct: ((r.start - minStart) / totalMs) * 100,
    widthPct: ((r.end - r.start) / totalMs) * 100,
  }))
})
</script>
