<template>
  <div class="rounded overflow-hidden bg-taaffeite/6 dark:bg-taaffeite/10 border border-taaffeite/20 dark:border-taaffeite/25 flex flex-col gap-2 p-2 min-w-0">
    <div v-if="loading" class="text-[11px] text-warm-400">{{ t("common.loading") }}</div>
    <div v-else-if="error" class="text-[11px] text-coral">{{ error }}</div>
    <template v-else>
      <div class="max-h-72 overflow-y-auto flex flex-col gap-2 min-w-0">
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
})

const { t } = useI18n()
const loading = ref(false)
const error = ref("")
const messages = ref([])
const canReceive = ref(false)
const sendText = ref("")
const sending = ref(false)
const expandedSystem = ref(new Set())
const expandedTools = ref(new Set())
let timer = null

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

async function loadConversation({ silent = false } = {}) {
  if (!props.sessionId || !props.parent) {
    error.value = t("chat.subagent.unavailable")
    return
  }
  if (!silent) loading.value = true
  error.value = ""
  try {
    const ident = identifier()
    const data = props.live
      ? await terrariumAPI.getSubagentConversation(props.sessionId, props.parent, ident)
      : await sessionAPI.getSubagentConversation(props.sessionId, {
          parent: props.parent,
          ...ident,
        })
    messages.value = data.messages || []
    canReceive.value = props.live && !!data.can_receive
  } catch (err) {
    if (silent) return
    error.value = err?.response?.data?.detail || t("chat.subagent.unavailable")
    messages.value = []
    canReceive.value = false
  } finally {
    if (!silent) loading.value = false
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
  if (timer) clearInterval(timer)
  timer = null
}

function startPolling() {
  if (timer || !props.live || !isLive()) return
  timer = setInterval(() => {
    if (isLive()) loadConversation({ silent: true })
    else stopPolling()
  }, 1500)
}

watch(
  () => props.status,
  (status, previous) => {
    if (previous === "running" && status !== "running") loadConversation({ silent: true })
    if (status === "running") startPolling()
  },
)

onMounted(async () => {
  await loadConversation()
  startPolling()
})
onUnmounted(stopPolling)
</script>
