<template>
  <header class="px-4 py-2 border-b border-warm-200 dark:border-warm-700 bg-warm-50 dark:bg-warm-950">
    <div class="flex items-center gap-2 text-sm">
      <span class="w-2 h-2 rounded-full" :class="statusColor" />
      <span class="font-medium text-warm-800 dark:text-warm-200">{{ name }}</span>
      <span class="text-warm-500">·</span>
      <span class="text-warm-500">{{ kind }}</span>
      <span class="text-warm-500">·</span>
      <span class="text-warm-600 dark:text-warm-400">{{ statusLabel }}</span>
      <span v-if="liveMismatch" class="ml-auto text-[10px] uppercase tracking-wider text-amber" :title="mismatchTitle"> live data: chat tab not open </span>
    </div>
    <div class="flex items-center gap-3 text-xs text-warm-500 mt-1">
      <span v-if="model">{{ model }}</span>
      <span v-if="contextPercent > 0">{{ contextPercent }}% ctx</span>
      <span v-if="costLine">${{ costLine }} spent</span>
      <span v-if="jobCount > 0">{{ jobCount }} job{{ jobCount === 1 ? "" : "s" }}</span>
      <span v-if="age">{{ age }}</span>
    </div>
  </header>
</template>

<script setup>
import { computed } from "vue"

import { useStatusStore } from "@/stores/status"

const props = defineProps({
  target: { type: String, required: true },
  instance: { type: Object, default: null },
})

const status = useStatusStore()

const name = computed(() => props.instance?.config_name ?? props.target)
const kind = computed(() => props.instance?.type ?? "agent")
const statusLabel = computed(() => props.instance?.status ?? "unknown")
const statusColor = computed(
  () =>
    ({
      running: "bg-iolite",
      paused: "bg-amber",
      stopped: "bg-warm-400",
      errored: "bg-coral",
    })[props.instance?.status] ?? "bg-warm-400",
)

const model = computed(() => status.sessionInfo.llmName || status.sessionInfo.model || props.instance?.model || "")

const contextPercent = computed(() => Math.round(status.tokenUsage.contextPercent || 0))

const costLine = computed(() => {
  const cost = props.instance?.cost ?? 0
  return cost > 0 ? cost.toFixed(2) : ""
})

const jobCount = computed(() => status.runningJobs.length)

const age = computed(() => {
  const start = props.instance?.created_at ? new Date(props.instance.created_at).getTime() : status.sessionInfo.startTime
  if (!start) return ""
  const sec = Math.floor((Date.now() - start) / 1000)
  if (sec < 60) return `${sec}s ago`
  const m = Math.floor(sec / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m ago`
})

// Status store reflects whichever creature chat is currently bound to
// (singleton WS until Phase 5). If it's a different target, indicate
// the mismatch so the user knows live values may be stale.
const liveMismatch = computed(() => {
  if (!status.sessionInfo.sessionId) return false
  return status.sessionInfo.sessionId !== props.target
})

const mismatchTitle = computed(() => `Live status data is currently bound to ${status.sessionInfo.sessionId}; this Inspector is for ${props.target}. Open a chat tab for ${props.target} to see live data.`)
</script>
