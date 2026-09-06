<template>
  <div :class="['rounded overflow-hidden bg-taaffeite/6 dark:bg-taaffeite/10 border border-taaffeite/20 dark:border-taaffeite/25 flex flex-col gap-2 p-2 min-w-0', fill ? 'h-full min-h-0' : '']">
    <div v-if="showBack" class="shrink-0">
      <button data-test="subagent-back" class="text-[11px] text-warm-500 hover:text-warm-700 dark:hover:text-warm-300" @click="$emit('back')">← {{ t("common.back") }}</button>
    </div>
    <div v-if="stage === 'selector' && candidates.length" :class="['flex flex-col gap-2', fill ? 'flex-1 min-h-0 overflow-y-auto' : '']">
      <div class="text-[11px] text-warm-500">{{ t("chat.subagent.chooseRun") }}</div>
      <button v-for="candidate in candidates" :key="`${candidate.member_sid || ''}:${candidate.parent}:${candidate.name}:${candidate.run}`" type="button" :disabled="loading" :data-test="`subagent-run-${candidate.run}`" class="rounded border border-iolite/20 bg-iolite/5 p-2 text-left hover:bg-iolite/10 disabled:opacity-50 disabled:cursor-not-allowed" @click.stop="selectCandidate(candidate)">
        <div class="flex items-center gap-2 text-[10px] font-mono text-warm-500">
          <span>{{ candidate.parent }} / {{ candidate.name }} / run {{ candidate.run }}</span>
          <span v-if="candidate.success === true" class="text-sage">success</span>
          <span v-else-if="candidate.success === false" class="text-coral">error</span>
          <span v-if="candidate.source" class="ml-auto">{{ candidate.source }}</span>
        </div>
        <div v-if="candidate.task" class="mt-1 text-[11px] text-warm-700 dark:text-warm-300">{{ candidate.task }}</div>
        <div v-if="candidate.output_preview" class="mt-1 text-[10px] text-warm-500 line-clamp-2">{{ candidate.output_preview }}</div>
        <div v-if="candidate.ts" class="mt-1 text-[9px] text-warm-400">{{ formatTimestamp(candidate.ts) }}</div>
      </button>
    </div>
    <div v-else-if="loading && !blocks.length" class="text-[11px] text-warm-400">{{ t("common.loading") }}</div>
    <div v-else-if="stage === 'selector'" class="text-[11px] text-warm-400 italic">{{ t("chat.subagent.empty") }}</div>
    <div v-else-if="error" class="text-[11px] text-coral">{{ error }}</div>
    <template v-else>
      <div v-if="selectorAvailable" class="shrink-0">
        <button data-test="subagent-back-to-runs" class="text-[11px] text-iolite hover:underline" @click.stop="backToRuns">← {{ t("chat.subagent.backToRuns") }}</button>
      </div>
      <div data-test="subagent-messages" :class="['flex flex-col gap-2 min-w-0', fill ? 'flex-1 min-h-0 overflow-y-auto' : 'max-h-72 overflow-y-auto']">
        <div v-for="(item, i) in blocks" :key="i" class="min-w-0">
          <template v-if="item.kind === 'system'">
            <button class="w-full flex items-center gap-1.5 text-left text-[10px] text-warm-400 hover:text-warm-500" @click.stop="toggleSystem(i)">
              <span class="i-carbon-chevron-right text-[9px] transition-transform shrink-0" :class="{ 'rotate-90': expandedSystem.has(i) }" />
              <span class="font-mono uppercase">system</span>
              <span class="italic">prompt ({{ item.text.length }} chars)</span>
            </button>
            <pre v-if="expandedSystem.has(i)" class="mt-1 font-mono whitespace-pre-wrap break-all max-h-40 overflow-y-auto bg-warm-100/70 dark:bg-warm-900/50 rounded px-2 py-1 text-[10px] text-warm-500 dark:text-warm-400">{{ item.text }}</pre>
          </template>

          <div v-else-if="item.kind === 'user'" class="ml-auto max-w-[85%] rounded-lg bg-warm-100 dark:bg-warm-800/80 border border-warm-200/60 dark:border-warm-700/60 px-2.5 py-1.5 min-w-0">
            <div class="text-[9px] uppercase tracking-wide text-warm-400 mb-0.5">user</div>
            <div v-if="item.parts" class="flex flex-col gap-1 text-body">
              <template v-for="(part, pi) in item.parts" :key="pi">
                <MarkdownRenderer v-if="part.type === 'text' && part.text" :content="part.text" />
                <img v-else-if="part.type === 'image_url'" :src="part.image_url?.url" class="tool-inline-image" />
              </template>
            </div>
            <div v-else class="text-body"><MarkdownRenderer :content="item.content" /></div>
          </div>

          <div v-else class="max-w-[92%] min-w-0">
            <div class="text-[9px] uppercase tracking-wide text-warm-400 mb-0.5">assistant</div>
            <div v-if="item.parts" class="flex flex-col gap-1 text-body">
              <template v-for="(part, pi) in item.parts" :key="pi">
                <MarkdownRenderer v-if="part.type === 'text' && part.text" :content="part.text" />
                <img v-else-if="part.type === 'image_url'" :src="part.image_url?.url" class="tool-inline-image" />
              </template>
            </div>
            <div v-else-if="item.content" class="text-body"><MarkdownRenderer :content="item.content" /></div>
            <div v-if="item.toolCalls.length" class="flex flex-col gap-1.5 mt-1.5 min-w-0">
              <ToolCallBlock v-for="call in item.toolCalls" :key="call.id" :tc="call" :depth="depth + 1" :expanded="expandedTools.has(call.id)" @toggle="toggleTool(call.id)" />
            </div>
          </div>
        </div>
        <div v-if="!blocks.length" class="text-[11px] text-warm-400 italic">{{ t("chat.subagent.empty") }}</div>
      </div>
      <div v-if="canReceive" class="flex items-end gap-2 pt-1 border-t border-taaffeite/15 dark:border-taaffeite/20">
        <textarea v-model="sendText" rows="1" :placeholder="t('chat.subagent.placeholder')" class="flex-1 min-w-0 resize-none rounded border border-warm-200 dark:border-warm-700 bg-warm-50 dark:bg-warm-950 px-2 py-1 text-[11px] text-warm-800 dark:text-warm-200 placeholder-warm-400 dark:placeholder-warm-500 focus:outline-none focus:border-taaffeite" @keydown.enter.exact.prevent="submitSend" />
        <button class="text-[11px] px-2 py-1 rounded bg-taaffeite/20 text-taaffeite-shadow dark:text-taaffeite-light hover:bg-taaffeite/30 disabled:opacity-50 shrink-0" :disabled="sending || !sendText.trim()" @click.stop="submitSend">{{ t("chat.subagent.send") }}</button>
      </div>
      <div v-else class="text-[10px] text-warm-400 italic">{{ t("chat.subagent.readOnly") }}</div>
    </template>
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref, watch } from "vue"

import MarkdownRenderer from "@/components/common/MarkdownRenderer.vue"
import { sessionAPI, terrariumAPI } from "@/utils/api"
import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useI18n } from "@/utils/i18n"

const ToolCallBlock = defineAsyncComponent(() => import("@/components/chat/ToolCallBlock.vue"))

const props = defineProps({
  sessionId: { type: String, required: true },
  parent: { type: String, required: true },
  jobId: { type: String, default: "" },
  name: { type: String, default: "" },
  run: { type: [String, Number], default: null },
  live: { type: Boolean, default: false },
  status: { type: String, default: "" },
  depth: { type: Number, default: 0 },
  fill: { type: Boolean, default: false },
  showBack: { type: Boolean, default: false },
})

defineEmits(["back"])

const { t } = useI18n()
const loading = ref(false)
const error = ref("")
const messages = ref([])
const candidates = ref([])
const canReceive = ref(false)
const sendText = ref("")
const sending = ref(false)
const expandedSystem = ref(new Set())
const expandedTools = ref(new Set())
const stage = ref("conversation")
const selectorAvailable = ref(false)
let timer = null
// Out-of-order guard: only the newest target/request may mutate state,
// and nothing may mutate after the panel is destroyed.
let requestGeneration = 0
let disposed = false

function backToRuns() {
  stage.value = "selector"
  if (!candidates.value.length) {
    const generation = ++requestGeneration
    loadCandidates().catch(() => {
      if (!disposed && generation === requestGeneration) {
        error.value = t("chat.subagent.unavailable")
      }
    })
  }
}

function messageText(message) {
  if (typeof message?.content === "string") return message.content
  if (!Array.isArray(message?.content)) return ""
  return message.content
    .filter((part) => part?.type === "text")
    .map((part) => part.text || "")
    .join("\n")
}

function messageParts(message) {
  return Array.isArray(message?.content) ? message.content : null
}

function parseArgs(raw) {
  if (!raw) return {}
  if (typeof raw !== "string") return raw
  try {
    return JSON.parse(raw)
  } catch {
    return { raw }
  }
}

const blocks = computed(() => {
  const resultById = {}
  for (const message of messages.value) {
    if (message?.role === "tool" && message.tool_call_id != null) {
      resultById[message.tool_call_id] = messageText(message)
    }
  }
  const items = []
  messages.value.forEach((message, messageIndex) => {
    if (message?.role === "tool") return
    if (message?.role === "system") {
      items.push({ kind: "system", text: messageText(message) })
      return
    }
    if (message?.role === "user") {
      items.push({ kind: "user", content: messageText(message), parts: messageParts(message) })
      return
    }
    const toolCalls = (message?.tool_calls || []).map((call, callIndex) => ({
      type: "tool",
      id: call.id || `sa_${messageIndex}_${callIndex}`,
      name: call.function?.name || "tool",
      kind: "tool",
      args: parseArgs(call.function?.arguments),
      status: "done",
      result: call.id != null ? resultById[call.id] || "" : "",
      children: [],
    }))
    items.push({
      kind: "assistant",
      content: messageText(message),
      parts: messageParts(message),
      toolCalls,
    })
  })
  return items
})

function toggleSystem(index) {
  const next = new Set(expandedSystem.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  expandedSystem.value = next
}

function toggleTool(id) {
  const next = new Set(expandedTools.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedTools.value = next
}

function identifier() {
  if (props.jobId) return { jobId: props.jobId, ...(props.name ? { name: props.name } : {}) }
  const result = { name: props.name }
  if (props.run != null) result.run = props.run
  return result
}

function formatTimestamp(value) {
  const millis = Number(value) < 10_000_000_000 ? Number(value) * 1000 : Number(value)
  const date = new Date(millis)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString()
}

async function loadCandidates() {
  const generation = requestGeneration
  const data = await sessionAPI.listSubagents(props.sessionId, {
    ...(props.parent ? { parent: props.parent } : {}),
    ...(props.jobId ? { jobId: props.jobId } : {}),
    ...(props.name ? { name: props.name } : {}),
  })
  if (disposed || generation !== requestGeneration) return
  const runs = data.runs || []
  candidates.value = runs.some((candidate) => candidate.member_sid) ? [] : runs
  if (candidates.value.length) {
    selectorAvailable.value = true
    stage.value = "selector"
  }
}

async function selectCandidate(candidate) {
  if (loading.value) return
  const generation = ++requestGeneration
  loading.value = true
  error.value = ""
  expandedSystem.value = new Set()
  expandedTools.value = new Set()
  try {
    const data = await sessionAPI.getSubagentConversation(props.sessionId, {
      parent: candidate.parent,
      name: candidate.name,
      run: candidate.run,
    })
    if (disposed || generation !== requestGeneration) return
    messages.value = data.messages || []
    canReceive.value = false
    stage.value = "conversation"
  } catch (err) {
    if (disposed || generation !== requestGeneration) return
    error.value = err?.response?.data?.detail || t("chat.subagent.unavailable")
  } finally {
    if (!disposed && generation === requestGeneration) loading.value = false
  }
}

async function loadConversation({ silent = false } = {}) {
  if (!props.sessionId || !props.parent) {
    if (!silent) error.value = t("chat.subagent.unavailable")
    return
  }
  const generation = requestGeneration
  if (!silent) loading.value = true
  error.value = ""
  candidates.value = []
  selectorAvailable.value = false
  try {
    const ident = identifier()
    const data = props.live
      ? await terrariumAPI.getSubagentConversation(props.sessionId, props.parent, ident)
      : await sessionAPI.getSubagentConversation(props.sessionId, {
          parent: props.parent,
          ...ident,
        })
    if (disposed || generation !== requestGeneration) return
    messages.value = data.messages || []
    canReceive.value = props.live && !!data.can_receive
    stage.value = "conversation"
  } catch (err) {
    if (silent || disposed || generation !== requestGeneration) return
    messages.value = []
    canReceive.value = false
    if (!props.live && err?.response?.status === 409) {
      try {
        await loadCandidates()
        if (candidates.value.length) return
      } catch {
        candidates.value = []
      }
    }
    if (disposed || generation !== requestGeneration) return
    error.value = err?.response?.data?.detail || t("chat.subagent.unavailable")
  } finally {
    if (!disposed && generation === requestGeneration && !silent) loading.value = false
  }
}

async function submitSend() {
  const content = sendText.value.trim()
  if (!content || sending.value || !props.live) return
  sending.value = true
  error.value = ""
  try {
    await terrariumAPI.sendSubagentMessage(props.sessionId, props.parent, props.name, content, props.jobId)
    sendText.value = ""
    await loadConversation()
  } catch (err) {
    error.value = err?.response?.data?.detail || t("chat.subagent.sendFailed")
  } finally {
    sending.value = false
  }
}

function isLive() {
  return props.status === "running" || canReceive.value
}

function stopPolling() {
  if (timer) timer.stop()
  timer = null
}

function startPolling() {
  if (timer || !props.live || !isLive()) return
  timer = createVisibilityInterval(() => {
    if (!isLive()) {
      stopPolling()
      return
    }
    // Returning the promise lets the interval skip ticks while a slow
    // conversation read is in flight: this is the hottest poll in the
    // app (1.5 s) and every request reopens the session store.
    return loadConversation({ silent: true })
  }, 1500)
  timer.start()
}

watch(
  () => props.status,
  (status, previous) => {
    if (previous === "running" && status !== "running") loadConversation({ silent: true })
    if (status === "running") startPolling()
  },
)

watch([() => props.sessionId, () => props.parent, () => props.jobId, () => props.name], () => {
  stopPolling()
  requestGeneration += 1
  messages.value = []
  candidates.value = []
  error.value = ""
  sendText.value = ""
  selectorAvailable.value = false
  stage.value = "conversation"
  expandedSystem.value = new Set()
  expandedTools.value = new Set()
  loadConversation().then(() => {
    if (!disposed) startPolling()
  })
})

onMounted(async () => {
  await loadConversation()
  if (!disposed) startPolling()
})
onUnmounted(() => {
  disposed = true
  stopPolling()
})
</script>
