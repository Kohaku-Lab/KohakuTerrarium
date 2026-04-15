<template>
  <!--
    Panel bg = recessed surface (warm-100 / warm-900)
    Bubble bg = header-level surface (white / warm-800)
    Tab bar sits on panel bg, active tab = bubble bg
    Bubble has equal margin left/right/bottom
  -->
  <div class="h-full flex flex-col bg-warm-100 dark:bg-[#211F1D]">
    <!-- Tab bar on panel bg -->
    <div role="tablist" class="flex items-end gap-0 px-4 pt-2 shrink-0">
      <div v-for="tab in chat.tabs" :key="tab" role="tab" tabindex="0" :aria-selected="chat.activeTab === tab" class="relative flex items-center gap-1.5 px-3.5 py-2 text-xs font-medium cursor-pointer select-none rounded-t-lg -mb-px transition-colors" :class="chat.activeTab === tab ? 'bg-white dark:bg-warm-900 text-warm-800 dark:text-warm-200 border border-warm-200 dark:border-warm-700 border-b-white dark:border-b-warm-900 z-10' : 'text-warm-400 dark:text-warm-500 hover:text-warm-600 dark:hover:text-warm-400 border border-transparent'" @click="chat.setActiveTab(tab)" @keydown.enter="chat.setActiveTab(tab)" @keydown.space.prevent="chat.setActiveTab(tab)">
        <template v-if="tab === 'root'">
          <span class="w-2 h-2 rounded-full bg-amber shrink-0" />
          <span>Root Agent</span>
        </template>
        <template v-else-if="tab.startsWith('ch:')">
          <span class="text-aquamarine font-bold shrink-0">&rarr;</span>
          <span>{{ tab.slice(3) }}</span>
          <span v-if="chat.unreadCounts[tab]" class="ml-1 px-1.5 py-0.5 rounded-full bg-amber text-white text-[9px] font-bold leading-none">{{ chat.unreadCounts[tab] }}</span>
        </template>
        <template v-else>
          <StatusDot :status="getCreatureStatus(tab)" />
          <span>{{ tab }}</span>
        </template>

        <button v-if="tab !== 'root' && chat.tabs.length > 1" class="ml-1 w-4 h-4 flex items-center justify-center rounded-sm text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors" :aria-label="`Close ${tab} tab`" @click.stop="closeTab(tab)">
          <div class="i-carbon-close text-[10px]" />
        </button>
      </div>

      <!-- Token usage + session info for active tab -->
      <div v-if="activeTokens > 0 || chat.sessionInfo.model" class="flex items-center gap-2 px-2 py-2 -mb-px text-[10px] text-warm-400 font-mono">
        <template v-if="chat.sessionInfo.model">
          <span class="text-warm-500 dark:text-warm-400">{{ chat.sessionInfo.model }}</span>
          <span class="text-warm-300 dark:text-warm-600">|</span>
        </template>
        <template v-if="activeTokens > 0">
          <span class="i-carbon-meter text-amber" />
          <span title="Cumulative input tokens">In: {{ formatTokens(activeUsage.prompt) }}</span>
          <span v-if="activeUsage.cached > 0" class="text-aquamarine" title="Cached input tokens">(cache {{ formatTokens(activeUsage.cached) }})</span>
          <span title="Cumulative output tokens">Out: {{ formatTokens(activeUsage.completion) }}</span>
        </template>
        <template v-if="chat.sessionInfo.compactThreshold > 0 && activeUsage.prompt > 0">
          <span class="text-warm-300 dark:text-warm-600">|</span>
          <span :class="contextPct >= 80 ? 'text-coral' : contextPct >= 60 ? 'text-amber' : ''" :title="`Context: ${formatTokens(activeUsage.lastPrompt || 0)} / ${formatTokens(chat.sessionInfo.compactThreshold)}`">Ctx: {{ contextPct }}%</span>
        </template>
      </div>

      <!-- Tab bar bottom border (bubble top border) -->
      <div class="flex-1 border-b border-b-warm-200 dark:border-b-warm-700" />
    </div>

    <!-- Chat bubble: surface-level bg, equal margin left/right/bottom -->
    <div class="flex-1 mx-4 mb-4 bg-white dark:bg-warm-900 rounded-b-xl rounded-tr-xl border border-warm-200 dark:border-warm-700 border-t-0 overflow-hidden flex flex-col shadow-sm">
      <!-- Decorative top accent: subtle gem gradient -->
      <div class="h-0.5 w-full bg-gradient-to-r from-iolite/30 via-taaffeite/20 to-aquamarine/30" />

      <!-- Messages -->
      <div ref="messagesEl" class="flex-1 overflow-y-auto px-5 py-4" @scroll="onMessagesScroll">
        <div class="flex flex-col gap-3">
          <template v-if="chat.currentMessages.length === 0">
            <div class="text-center py-16">
              <div class="w-12 h-12 rounded-2xl bg-gradient-to-br from-iolite/10 to-amber/10 dark:from-iolite/5 dark:to-amber/5 flex items-center justify-center mx-auto mb-3">
                <div class="i-carbon-chat text-xl text-iolite/40 dark:text-iolite-light/30" />
              </div>
              <p class="text-warm-400 dark:text-warm-500 text-sm">{{ emptyTitle }}</p>
              <p class="text-warm-300 dark:text-warm-600 text-xs mt-1">{{ emptySubtitle }}</p>
            </div>
          </template>
          <ChatMessage v-for="(msg, idx) in chat.currentMessages" :key="msg.id" :message="msg" :prev-message="idx > 0 ? chat.currentMessages[idx - 1] : null" :is-first="idx === 0" :message-idx="idx" :is-last-assistant="msg.role === 'assistant' && idx === chat.currentMessages.length - 1" />
          <div v-if="chat.processing" class="flex items-center gap-2.5 py-2 pl-1">
            <span class="w-2 h-2 rounded-full bg-amber kohaku-glow" />
            <span class="text-sm text-amber/80 kohaku-pulse">KohakUwUing...</span>
          </div>
        </div>
      </div>

      <!-- Queued messages: shown above input, not in main chat -->
      <div v-if="!readOnly && chat.queuedMessages.length" class="px-4 pt-2 flex flex-col gap-1.5">
        <div v-for="qm in chat.queuedMessages" :key="qm.id" class="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-amber/5 dark:bg-amber/5 border border-amber/20 text-sm">
          <span class="i-carbon-time text-amber/60 text-xs flex-shrink-0" />
          <span class="text-warm-500 dark:text-warm-400 truncate">{{ qm.content }}</span>
          <span class="text-warm-300 dark:text-warm-600 text-xs flex-shrink-0 ml-auto">queued</span>
        </div>
      </div>

      <!-- Input: sits inside bubble, with subtle top border -->
      <div v-if="!readOnly" class="px-4 pb-4 pt-2 border-t border-t-warm-100 dark:border-t-warm-800">
        <div class="flex gap-2 px-3 py-1.5 rounded-xl bg-warm-50 dark:bg-warm-800 border border-warm-200 dark:border-warm-700 focus-within:border-iolite/40 dark:focus-within:border-iolite-light/30 transition-colors" :class="inputText.includes('\n') ? 'items-end' : 'items-center'">
          <textarea ref="inputEl" v-model="inputText" rows="1" class="flex-1 bg-transparent border-none outline-none text-sm text-warm-800 dark:text-warm-200 placeholder-warm-400 dark:placeholder-warm-500 resize-none max-h-32 leading-relaxed py-1" style="min-height: 2em" :placeholder="inputPlaceholder" @keydown="onInputKeydown" @input="autoResize" />
          <!-- Compact/Clear actions -->
          <button class="w-7 h-7 flex items-center justify-center rounded-md transition-colors shrink-0 text-warm-400 hover:text-iolite dark:hover:text-iolite-light hover:bg-iolite/10" title="Compact context" aria-label="Compact context" @click="triggerCompact">
            <span class="i-carbon-collapse-all text-xs" />
          </button>
          <button class="w-7 h-7 flex items-center justify-center rounded-md transition-colors shrink-0 text-warm-400 hover:text-coral hover:bg-coral/10" title="Clear context" aria-label="Clear context" @click="triggerClear">
            <span class="i-carbon-clean text-xs" />
          </button>
          <button v-if="chat.processing || chat.hasRunningJobs" class="w-8 h-8 flex items-center justify-center rounded-lg transition-all shrink-0 mb-0.5 bg-coral/90 text-white hover:bg-coral shadow-sm shadow-coral/20" title="Stop generation (Esc)" aria-label="Stop generation" @click="chat.interrupt()">
            <span class="i-carbon-stop-filled text-sm" />
          </button>
          <button v-else class="w-8 h-8 flex items-center justify-center rounded-lg transition-all shrink-0 mb-0.5" :class="inputText.trim() ? 'bg-iolite text-white hover:bg-iolite-shadow shadow-sm shadow-iolite/20' : 'text-warm-300 dark:text-warm-600 cursor-not-allowed'" :disabled="!inputText.trim()" aria-label="Send message" @click="send">
            <span class="i-carbon-send text-sm" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import StatusDot from "@/components/common/StatusDot.vue"
import ChatMessage from "@/components/chat/ChatMessage.vue"
import { useChatStore } from "@/stores/chat"
import { terrariumAPI, agentAPI } from "@/utils/api"
import { getHybridPref, removeHybridPref, setHybridPref } from "@/utils/uiPrefs"

const props = defineProps({
  instance: { type: Object, required: true },
  readOnly: { type: Boolean, default: false },
  emptyTitle: { type: String, default: "No messages yet" },
  emptySubtitle: { type: String, default: "Send a message to get started" },
})

const chat = useChatStore()
const inputText = ref("")
const messagesEl = ref(null)
const inputEl = ref(null)

function draftKey() {
  const instanceId = props.instance?.id || chat._instanceId || ""
  const tab = chat.activeTab || ""
  if (!instanceId || !tab || props.readOnly) return ""
  return `kt.chat.draft.${instanceId}.${tab}`
}

async function restoreDraft() {
  const key = draftKey()
  if (!key) {
    inputText.value = ""
    return
  }
  inputText.value = (await getHybridPref(key, "")) || ""
  nextTick(autoResize)
}

function persistDraft() {
  const key = draftKey()
  if (!key) return
  if (inputText.value) setHybridPref(key, inputText.value)
  else removeHybridPref(key)
}

const activeUsage = computed(() => {
  const tab = chat.activeTab
  if (!tab) return { prompt: 0, completion: 0, total: 0 }
  return chat.tokenUsage[tab] || { prompt: 0, completion: 0, total: 0 }
})

const activeTokens = computed(() => activeUsage.value.total)

const contextPct = computed(() => {
  const threshold = chat.sessionInfo.compactThreshold
  const lastPrompt = activeUsage.value.lastPrompt || 0
  if (!threshold || !lastPrompt) return 0
  return Math.round((lastPrompt / threshold) * 100)
})

function formatTokens(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M"
  if (n >= 1000) return (n / 1000).toFixed(1) + "K"
  return String(n)
}

const inputPlaceholder = computed(() => {
  if (!chat.activeTab) return "Select a tab..."
  if (chat.activeTab.startsWith("ch:")) return `Send to ${chat.activeTab.slice(3)} channel...`
  return "Message ..."
})

function getCreatureStatus(name) {
  const creature = props.instance.creatures.find((c) => c.name === name)
  return creature?.status || "idle"
}

function closeTab(tab) {
  if (props.readOnly) return
  chat.closeTab(tab)
}

function onInputKeydown(e) {
  if (props.readOnly) return
  // Skip if IME composition is active (e.g. Chinese/Japanese/Korean input).
  // During composition, Enter confirms the selected candidate — not send.
  if (e.isComposing || e.keyCode === 229) return

  if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey) {
    e.preventDefault()
    send()
  }
  // Shift+Enter and Ctrl+Enter insert newline (default textarea behavior)
}

function autoResize() {
  const el = inputEl.value
  if (!el) return
  el.style.height = "auto"
  el.style.height = Math.min(el.scrollHeight, 128) + "px"
}

// Auto-scroll: only when new visible content arrives and the user is already near bottom.
const isNearBottom = ref(true)
const forceScrollOnNextMessageUpdate = ref(true)
const scrollPositions = new Map()

function getScrollKey(instanceId = props.instance?.id || chat._instanceId, tab = chat.activeTab) {
  if (!instanceId || !tab) return ""
  return `${instanceId}:${tab}`
}

function updateNearBottom() {
  const el = messagesEl.value
  if (!el) return
  isNearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

function saveScrollPosition(instanceId = props.instance?.id || chat._instanceId, tab = chat.activeTab) {
  const el = messagesEl.value
  const key = getScrollKey(instanceId, tab)
  if (!el || !key) return
  scrollPositions.set(key, el.scrollTop)
}

function restoreScrollPosition(instanceId = props.instance?.id || chat._instanceId, tab = chat.activeTab) {
  const el = messagesEl.value
  const key = getScrollKey(instanceId, tab)
  if (!el || !key) return false
  const saved = scrollPositions.get(key)
  if (saved == null) {
    el.scrollTop = el.scrollHeight
    updateNearBottom()
    return false
  }
  el.scrollTop = Math.max(0, Math.min(saved, el.scrollHeight - el.clientHeight))
  updateNearBottom()
  return true
}

function onMessagesScroll() {
  updateNearBottom()
  saveScrollPosition()
}

function scrollToBottom() {
  const el = messagesEl.value
  if (!el) return
  el.scrollTop = el.scrollHeight
  updateNearBottom()
  saveScrollPosition()
}

const messageTailSignature = computed(() => {
  const messages = chat.currentMessages
  const last = messages[messages.length - 1]
  if (!last) return "0"
  const contentLen = typeof last.content === "string" ? last.content.length : 0
  const parts = Array.isArray(last.parts)
    ? last.parts
        .map((part) => {
          if (part.type === "text") return `t:${part.content?.length || 0}`
          return `o:${part.status || ""}:${part.result?.length || 0}:${part.children?.length || 0}`
        })
        .join("|")
    : ""
  return `${messages.length}:${last.id}:${last.role}:${contentLen}:${parts}`
})

watch(messageTailSignature, (nextSig, prevSig) => {
  if (!prevSig || nextSig === prevSig) return
  if (forceScrollOnNextMessageUpdate.value || isNearBottom.value) {
    forceScrollOnNextMessageUpdate.value = false
    nextTick(scrollToBottom)
  }
})

watch(
  () => chat.processing,
  (val) => {
    if (val && isNearBottom.value) {
      nextTick(scrollToBottom)
    }
  },
)

watch(
  () => [props.instance?.id, chat.activeTab],
  ([instanceId, tab], [prevInstanceId, prevTab]) => {
    if (prevInstanceId && prevTab) saveScrollPosition(prevInstanceId, prevTab)
    restoreDraft()
    nextTick(() => {
      const hadSavedScroll = restoreScrollPosition(instanceId, tab)
      forceScrollOnNextMessageUpdate.value = !hadSavedScroll
    })
  },
  { immediate: true },
)

watch(inputText, () => {
  persistDraft()
})

function send() {
  if (props.readOnly || !inputText.value.trim()) return
  chat.send(inputText.value)
  inputText.value = ""
  persistDraft()
  isNearBottom.value = true // force scroll after send
  nextTick(() => {
    if (inputEl.value) inputEl.value.style.height = "auto"
    scrollToBottom()
  })
}

async function triggerCompact() {
  if (props.readOnly) return
  try {
    const tab = chat.activeTab
    if (chat._instanceType === "terrarium") {
      await terrariumAPI.executeCreatureCommand(chat._instanceId, tab || "root", "compact")
    } else {
      await agentAPI.executeCommand(chat._instanceId, "compact")
    }
  } catch (err) {
    console.error("Compact failed:", err)
  }
}

async function triggerClear() {
  if (props.readOnly) return
  if (!confirm("Clear conversation context? Chat history will be preserved in the session.")) return
  try {
    const tab = chat.activeTab
    if (chat._instanceType === "terrarium") {
      await terrariumAPI.executeCreatureCommand(chat._instanceId, tab || "root", "clear", "--force")
    } else {
      await agentAPI.executeCommand(chat._instanceId, "clear", "--force")
    }
  } catch (err) {
    console.error("Clear failed:", err)
  }
}

async function stopTask(jobId, jobName) {
  try {
    const tab = chat.activeTab
    if (chat._instanceType === "terrarium") {
      await terrariumAPI.stopCreatureTask(chat._instanceId, tab || "root", jobId)
    } else {
      await agentAPI.stopTask(chat._instanceId, jobId)
    }
    // Don't eagerly remove from runningJobs — the backend will send a
    // subagent_done/subagent_error or tool_done/tool_error event which
    // handles the removal properly. Just mark as cancelling for visual feedback.
    const job = chat.runningJobs[jobId]
    if (job) job.cancelling = true
  } catch (err) {
    console.error("Failed to stop task:", err)
  }
}

// Escape key interrupt
function onGlobalKeydown(e) {
  if (props.readOnly) return
  if (e.key === "Escape" && (chat.processing || chat.hasRunningJobs)) {
    chat.interrupt()
  }
}
onMounted(() => window.addEventListener("keydown", onGlobalKeydown))
onUnmounted(() => window.removeEventListener("keydown", onGlobalKeydown))
</script>
