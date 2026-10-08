<template>
  <div class="h-full flex flex-col min-h-0" data-test="v2-side-subagent">
    <div class="shrink-0 flex items-center gap-2 px-3 h-9 border-b kt-v2-line text-[11px] text-warm-500">
      <span class="i-carbon-bot text-taaffeite" />
      <span class="font-mono truncate">{{ tabLabel(payload.parent || "root", ctx.chat._rootSourceName) }} / {{ payload.name || "?" }}</span>
      <span v-if="payload.live && running" class="px-1.5 rounded-full bg-aquamarine/15 text-aquamarine text-[10px]">{{ t("side.subagent.live") }}</span>
      <span class="flex-1" />
      <span v-if="loading" class="i-carbon-circle-dash animate-spin" />
      <button class="i-carbon-renew hover:text-iolite" @click="load(false)" />
    </div>
    <div v-if="error" class="px-4 py-3 text-xs text-coral">{{ error }}</div>
    <div v-else-if="loading && !blocks.length" class="px-4 py-3 text-xs text-warm-400">{{ t("side.subagent.loading") }}</div>
    <div v-else class="flex-1 min-h-0 overflow-y-auto px-4 py-3 flex flex-col gap-3">
      <div v-if="!blocks.length" class="text-xs text-warm-400 italic">{{ t("side.subagent.empty") }}</div>
      <div v-for="(b, i) in blocks" :key="i" class="min-w-0">
        <template v-if="b.kind === 'system'">
          <button class="flex items-center gap-1.5 text-[11px] text-warm-400 hover:text-warm-600" @click="toggle(i)">
            <span class="i-carbon-chevron-right text-[10px] transition-transform" :class="{ 'rotate-90': expanded.has(i) }" />
            {{ t("side.subagent.system", { n: b.text.length }) }}
          </button>
          <pre v-if="expanded.has(i)" class="mt-1 max-h-60 overflow-y-auto rounded bg-warm-100 dark:bg-warm-800/70 px-2 py-1 font-mono text-[11px] text-warm-500 whitespace-pre-wrap break-words">{{ b.text }}</pre>
        </template>
        <div v-else-if="b.kind === 'user'" class="rounded-lg bg-warm-100 dark:bg-warm-800/80 px-3 py-2">
          <div class="text-[10px] uppercase tracking-wide text-warm-400 mb-0.5">{{ t("side.subagent.user") }}</div>
          <ConversationMessage :message="b.message" :render-text="renderText" bare />
        </div>
        <ConversationMessage v-else :message="b.message" :render-text="renderText" bare />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, h, onActivated, onBeforeUnmount, onDeactivated, ref, watch } from "vue"

import { ConversationMessage, MarkdownRenderer } from "@kohakuterrarium/chat-ui"
import { tabLabel } from "@/components/session-v2/model/creatureKeys"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { subagentBlocks } from "@/components/session-v2/model/widgets/subagentBlocks"
import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { sessionAPI, terrariumAPI } from "@/utils/api"

/**
 * One sub-agent run's full transcript beside the chat. `payload`: {parent,
 * jobId, name, run, status, live}. A live, running sub-agent refreshes
 * every 1.5 s (paused while the browser tab or the Chat tab is hidden).
 */
const props = defineProps({ payload: { type: Object, default: () => ({}) } })

const ctx = useSessionV2()
const t = useV2T()
const messages = ref([])
const loading = ref(false)
const error = ref("")
const expanded = ref(new Set())
let generation = 0
let inflight = null
let timer = null
let hidden = false

const blocks = computed(() => subagentBlocks(messages.value))
// Running while its job is still in the chat store's running list.
const running = computed(() => !!(props.payload.jobId && ctx.chat.runningJobs?.[props.payload.jobId]))

function renderText(content, breaks = false) {
  return h(MarkdownRenderer, { content, breaks })
}

function toggle(i) {
  const next = new Set(expanded.value)
  if (next.has(i)) next.delete(i)
  else next.add(i)
  expanded.value = next
}

function identifier() {
  const p = props.payload
  if (p.jobId) return { jobId: p.jobId, ...(p.name ? { name: p.name } : {}) }
  return { name: p.name, ...(p.run != null ? { run: p.run } : {}) }
}

async function fetchOnce(silent) {
  const mine = generation
  const sid = ctx.sessionId.value
  const parent = props.payload.parent || "root"
  if (!silent) loading.value = true
  try {
    const data = props.payload.live ? await terrariumAPI.getSubagentConversation(sid, parent, identifier()) : await sessionAPI.getSubagentConversation(sid, { parent, ...identifier() })
    if (mine !== generation) return
    messages.value = data?.messages || []
    error.value = ""
  } catch (err) {
    if (mine !== generation || silent) return
    error.value = err?.response?.data?.detail || t("side.subagent.unavailable")
  } finally {
    if (mine === generation && !silent) loading.value = false
  }
}

// Single flight: an overlapping poll tick reuses the read in flight.
function load(silent) {
  if (inflight) return inflight
  inflight = fetchOnce(silent).finally(() => (inflight = null))
  return inflight
}

function stopPolling() {
  timer?.stop()
  timer = null
}

function startPolling() {
  if (timer || hidden || !props.payload.live) return
  timer = createVisibilityInterval(() => {
    if (!running.value) {
      stopPolling()
      return load(true)
    }
    return load(true)
  }, 1500)
  timer.start()
}

watch(
  () => [ctx.sessionId.value, props.payload.parent, props.payload.jobId, props.payload.name, props.payload.run],
  () => {
    generation += 1
    inflight = null
    stopPolling()
    messages.value = []
    error.value = ""
    expanded.value = new Set()
    load(false)
    if (running.value) startPolling()
  },
  { immediate: true },
)
watch(running, (now) => {
  if (now) startPolling()
})
// Hidden with the Chat tab: no polling until it is shown again.
onDeactivated(() => {
  hidden = true
  stopPolling()
})
onActivated(() => {
  hidden = false
  if (!running.value) return
  load(true)
  startPolling()
})
onBeforeUnmount(() => {
  generation += 1
  stopPolling()
})
</script>
