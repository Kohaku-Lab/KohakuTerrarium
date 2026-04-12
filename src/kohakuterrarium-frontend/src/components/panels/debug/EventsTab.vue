<template>
  <div class="h-full flex flex-col overflow-hidden">
    <!-- Filter row -->
    <div
      class="flex items-center gap-2 px-3 py-1 border-b border-warm-200 dark:border-warm-700 shrink-0 text-[10px]"
    >
      <span class="text-warm-400">{{ events.length }} events</span>
      <el-input
        v-model="query"
        placeholder="filter..."
        size="small"
        clearable
        style="flex: 1; max-width: 320px"
      />
      <el-select
        v-model="typeFilter"
        placeholder="type"
        size="small"
        clearable
        style="width: 160px"
      >
        <el-option v-for="t in knownTypes" :key="t" :label="t" :value="t" />
      </el-select>
    </div>

    <!-- Events list -->
    <div class="flex-1 overflow-y-auto font-mono text-[10px]">
      <div
        v-for="(e, i) in visible"
        :key="i"
        class="px-3 py-1 border-b border-warm-200/50 dark:border-warm-700/50"
      >
        <div class="flex items-center gap-2">
          <span class="text-warm-400 shrink-0">{{ formatTs(e.timestamp) }}</span>
          <span class="shrink-0 px-1 rounded" :class="typeClass(e.role || e.type)">{{
            e.role || e.type || "?"
          }}</span>
          <span class="truncate text-warm-700 dark:text-warm-300">{{ eventPreview(e) }}</span>
          <span class="flex-1" />
          <button
            class="text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 shrink-0"
            :title="expanded === i ? 'Collapse' : 'Expand'"
            @click="expanded = expanded === i ? null : i"
          >
            <div
              :class="expanded === i ? 'i-carbon-chevron-up' : 'i-carbon-chevron-down'"
              class="text-[11px]"
            />
          </button>
        </div>
        <pre
          v-if="expanded === i"
          class="mt-1 text-[10px] bg-warm-100 dark:bg-warm-800 p-2 rounded whitespace-pre-wrap break-words text-warm-600 dark:text-warm-400"
          >{{ JSON.stringify(e, null, 2) }}</pre
        >
      </div>
      <div v-if="visible.length === 0" class="text-warm-400 text-center py-6">
        No events match the current filter
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import { useChatStore } from "@/stores/chat"

defineProps({
  instance: { type: Object, default: null },
})

const chat = useChatStore()

// Flatten every tab's messages, newest first.
const events = computed(() => {
  const out = []
  for (const msgs of Object.values(chat.messagesByTab || {})) {
    for (const m of msgs) out.push(m)
  }
  out.sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)))
  return out
})

const query = ref("")
const typeFilter = ref("")
const expanded = ref(/** @type {number | null} */ (null))

const knownTypes = computed(() => {
  const s = new Set()
  for (const e of events.value) {
    s.add(e.role || e.type || "")
  }
  return [...s].filter(Boolean).sort()
})

const visible = computed(() => {
  const q = query.value.trim().toLowerCase()
  return events.value.filter((e) => {
    if (typeFilter.value && (e.role || e.type) !== typeFilter.value) return false
    if (!q) return true
    const s = JSON.stringify(e).toLowerCase()
    return s.includes(q)
  })
})

function formatTs(ts) {
  if (!ts) return "—"
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString()
  } catch {
    return String(ts).slice(-8)
  }
}

function typeClass(t) {
  return (
    {
      user: "bg-iolite/10 text-iolite",
      assistant: "bg-aquamarine/10 text-aquamarine",
      compact: "bg-amber/10 text-amber",
      tool: "bg-warm-200 dark:bg-warm-800",
    }[t] || "bg-warm-100 dark:bg-warm-800 text-warm-500"
  )
}

function eventPreview(e) {
  if (typeof e.content === "string") return e.content.slice(0, 200)
  if (Array.isArray(e.tool_calls) && e.tool_calls.length) {
    return e.tool_calls.map((tc) => tc.name).join(", ")
  }
  if (e.summary) return e.summary.slice(0, 200)
  return ""
}
</script>
