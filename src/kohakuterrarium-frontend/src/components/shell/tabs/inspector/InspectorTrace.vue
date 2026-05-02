<template>
  <div class="h-full flex flex-col">
    <!-- Filter bar -->
    <div class="px-3 py-1.5 border-b border-warm-200 dark:border-warm-700 flex items-center gap-2 text-xs">
      <input v-model="filter" :placeholder="'Filter…'" class="px-2 py-0.5 rounded border border-warm-300 dark:border-warm-700 bg-transparent text-xs w-48" />
      <select v-model="typeFilter" class="text-xs bg-transparent border border-warm-300 dark:border-warm-700 rounded px-1">
        <option value="">all types</option>
        <option v-for="t in availableTypes" :key="t" :value="t">{{ t }}</option>
      </select>
      <span class="ml-auto text-warm-400">{{ filteredEvents.length }} / {{ status.recentEvents.length }}</span>
      <button class="i-carbon-pause" :class="paused ? 'text-coral' : 'text-warm-400 hover:text-warm-600'" :title="paused ? 'Resume' : 'Pause'" @click="paused = !paused" />
      <button class="i-carbon-clean text-warm-400 hover:text-warm-600" title="Clear buffer" @click="clearBuffer" />
    </div>

    <!-- Events list -->
    <div class="flex-1 overflow-y-auto font-mono text-xs">
      <div v-for="(ev, i) in displayedEvents" :key="i + '-' + ev.ts" class="px-3 py-1 border-b border-warm-100 dark:border-warm-800/50 flex items-start gap-2">
        <span class="text-warm-400 shrink-0">{{ formatTime(ev.ts) }}</span>
        <span class="shrink-0 text-iolite">{{ ev.type }}</span>
        <span v-if="ev.name" class="shrink-0 text-warm-700 dark:text-warm-300 truncate max-w-40">{{ ev.name }}</span>
        <span class="text-warm-500 truncate flex-1">{{ ev.detail || formatData(ev.data) }}</span>
      </div>
      <div v-if="displayedEvents.length === 0" class="p-4 text-warm-400 italic text-center">
        {{ status.recentEvents.length === 0 ? "No events yet — open a chat tab for this target to populate the live trace." : "No events match the current filter." }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import { useStatusStore } from "@/stores/status"

const props = defineProps({
  target: { type: String, required: true },
})

const status = useStatusStore()
const filter = ref("")
const typeFilter = ref("")
const paused = ref(false)
const frozen = ref([])

const availableTypes = computed(() => {
  const set = new Set()
  for (const ev of status.recentEvents) if (ev.type) set.add(ev.type)
  return [...set].sort()
})

const filteredEvents = computed(() => {
  return status.recentEvents.filter((ev) => {
    if (typeFilter.value && ev.type !== typeFilter.value) return false
    if (filter.value) {
      const q = filter.value.toLowerCase()
      const hay = `${ev.type} ${ev.name} ${ev.detail} ${JSON.stringify(ev.data)}`.toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })
})

const displayedEvents = computed(() => (paused.value ? frozen.value : filteredEvents.value))

function clearBuffer() {
  status.recentEvents = []
  if (paused.value) frozen.value = []
}

function formatTime(ts) {
  const d = new Date(ts)
  return d.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

function formatData(d) {
  if (!d || typeof d !== "object") return ""
  const parts = []
  for (const [k, v] of Object.entries(d)) {
    if (k === "activity_type") continue
    const s = typeof v === "string" ? v : JSON.stringify(v)
    if (s.length > 60) parts.push(`${k}=${s.slice(0, 60)}…`)
    else parts.push(`${k}=${s}`)
    if (parts.join(" ").length > 100) break
  }
  return parts.join(" ")
}

// When pause toggles on, freeze the current visible list.
import { watch } from "vue"
watch(paused, (p) => {
  if (p) frozen.value = [...filteredEvents.value]
})
</script>
