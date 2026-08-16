<template>
  <header class="px-4 py-2 border-b border-warm-200 dark:border-warm-700 bg-warm-50 dark:bg-warm-950">
    <div class="flex items-center gap-2 text-sm">
      <span class="w-2 h-2 rounded-full" :class="statusColor" />
      <span class="font-medium text-warm-800 dark:text-warm-200">{{ name }}</span>
      <span class="text-warm-500">·</span>
      <span class="text-warm-500">{{ kind }}</span>
      <span class="text-warm-500">·</span>
      <span class="text-warm-600 dark:text-warm-400">{{ statusLabel }}</span>
    </div>
    <div class="flex items-center gap-3 text-xs text-warm-500 mt-1">
      <span v-if="model">{{ model }}</span>
      <span v-if="contextPercent > 0">{{ contextPercent }}% ctx</span>
      <span v-if="tokenCount > 0">{{ tokenCount.toLocaleString() }} tok</span>
      <span v-if="costLine">${{ costLine }} spent</span>
      <span v-if="jobCount > 0" :title="jobTitle">{{ jobCount }} job{{ jobCount === 1 ? "" : "s" }} running</span>
      <span v-if="scratchpadCount > 0">{{ scratchpadCount }} scratchpad</span>
      <span v-if="age">{{ age }}</span>
    </div>
  </header>
</template>

<script setup>
import { computed } from "vue"

import { useChatStore } from "@/stores/chat"
import { useSessionDetailStore } from "@/stores/sessionDetail"
import { useStatusStore } from "@/stores/status"

const props = defineProps({
  target: { type: String, required: true },
  instance: { type: Object, default: null },
  // Session name the embedded viewer is scoped by — the header reads its
  // session-detail summary so its token total is the SAME backend
  // graph total the Cost/Overview tabs show. Defaults to the instance's
  // session id / target so the header still resolves standalone.
  sessionName: { type: String, default: "" },
})

const status = useStatusStore()
const chat = useChatStore()
// Prefer the backend graph total (all creatures + their sub-agents) from
// the same scoped session-detail summary the embedded Cost tab uses, so a
// hard refresh that only restored one creature's live usage into the chat
// store can't leave the header undercounting. Fall back to the live chat
// total until the first summary poll lands.
const detail = useSessionDetailStore(props.sessionName || props.instance?.session_id || props.target)
const tokenCount = computed(() => {
  const t = detail.summary?.totals?.tokens
  if (t) return (Number(t.prompt) || 0) + (Number(t.completion) || 0)
  return chat.sessionTokenTotals.prompt + chat.sessionTokenTotals.completion
})
const scratchpadCount = computed(() => Object.keys(status.scratchpad || {}).length)
const jobTitle = computed(() => status.runningJobs.map((j) => j.name).join(", "))

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

const model = computed(() => {
  const target = props.target || chat.activeTab
  const perTab = (target && chat.modelByTab[target]) || {}
  return perTab.llmName || perTab.model || chat.sessionInfo.llmName || chat.sessionInfo.model || status.sessionInfo.llmName || status.sessionInfo.model || props.instance?.model || ""
})

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
</script>
