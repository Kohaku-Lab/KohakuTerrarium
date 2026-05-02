<template>
  <div class="h-full flex flex-col">
    <!-- Header / filter bar -->
    <div class="px-3 py-1.5 border-b border-warm-200 dark:border-warm-700 flex items-center gap-2 text-xs">
      <button v-for="lvl in LEVELS" :key="lvl" class="px-2 py-0.5 rounded text-[10px] uppercase tracking-wider" :class="filter === lvl ? 'bg-iolite text-white' : 'text-warm-500 hover:text-warm-700 hover:bg-warm-200/50 dark:hover:bg-warm-800/50'" @click="filter = lvl">
        {{ lvl }}
      </button>
      <span class="ml-auto text-warm-400"> {{ filteredLines.length }} / {{ stream.lines.value.length }} lines </span>
      <button class="i-carbon-pause" :class="paused ? 'text-coral' : 'text-warm-400 hover:text-warm-600'" :title="paused ? 'Resume' : 'Pause'" @click="paused = !paused" />
      <button class="i-carbon-arrow-down" :class="autoscroll ? 'text-iolite' : 'text-warm-400 hover:text-warm-600'" :title="autoscroll ? 'Autoscroll on' : 'Autoscroll off'" @click="autoscroll = !autoscroll" />
      <button class="i-carbon-clean text-warm-400 hover:text-warm-600" title="Clear" @click="stream.clear()" />
    </div>

    <!-- Scrollback -->
    <div ref="scrollbox" class="flex-1 overflow-y-auto font-mono text-xs">
      <div v-if="!stream.connected.value" class="p-3 text-warm-400 italic">
        {{ stream.error.value || "Connecting to log stream…" }}
      </div>
      <div v-for="(line, i) in displayedLines" :key="i" class="px-3 py-0.5 flex gap-2" :class="lineColor(line.level)">
        <span class="text-warm-400 shrink-0">{{ line.ts }}</span>
        <span class="text-warm-500 shrink-0 max-w-32 truncate">{{ line.module }}</span>
        <span class="shrink-0 uppercase text-[10px] tracking-wider w-16">[{{ line.level }}]</span>
        <span class="flex-1 truncate">{{ line.text }}</span>
      </div>
      <div v-if="displayedLines.length === 0 && stream.connected.value" class="p-3 text-warm-400 italic text-center">No lines match the current filter.</div>
    </div>

    <!-- Source -->
    <div v-if="stream.meta.value" class="px-3 py-1 border-t border-warm-200 dark:border-warm-700 text-[10px] text-warm-400 flex gap-3">
      <span>pid {{ stream.meta.value.pid }}</span>
      <span class="truncate">{{ stream.meta.value.path }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue"

import { useLogStream } from "@/composables/useLogStream"

defineProps({ target: { type: String, required: true } })

// Backend emits levels as full lowercase words (`info`, `warning`,
// `error`, `debug`); we uppercase for display + comparison.
const LEVELS = ["ALL", "INFO", "WARNING", "ERROR"]
const filter = ref("ALL")
const paused = ref(false)
const autoscroll = ref(true)
const scrollbox = ref(null)
const frozen = ref([])

const stream = useLogStream()

const filteredLines = computed(() => {
  if (filter.value === "ALL") return stream.lines.value
  return stream.lines.value.filter((l) => (l.level || "info").toUpperCase() === filter.value)
})

const displayedLines = computed(() => (paused.value ? frozen.value : filteredLines.value))

watch(paused, (p) => {
  if (p) frozen.value = [...filteredLines.value]
})

watch(filteredLines, async () => {
  if (paused.value || !autoscroll.value) return
  await nextTick()
  if (scrollbox.value) scrollbox.value.scrollTop = scrollbox.value.scrollHeight
})

function lineColor(level) {
  switch ((level || "info").toUpperCase()) {
    case "WARN":
      return "text-amber"
    case "ERROR":
      return "text-coral"
    case "DEBUG":
      return "text-warm-500"
    default:
      return "text-warm-700 dark:text-warm-300"
  }
}
</script>
