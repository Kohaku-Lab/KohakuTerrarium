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
          <span>{{ t("common.rootAgent") }}</span>
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

        <button v-if="tab !== 'root' && chat.tabs.length > 1" class="ml-1 w-4 h-4 flex items-center justify-center rounded-sm text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors" :aria-label="t('chat.closeTab', { tab })" @click.stop="closeTab(tab)">
          <div class="i-carbon-close text-[10px]" />
        </button>
      </div>

      <!-- Model switcher — only mounted on compact density. The
           regular shell shows the switcher in StatusBar at the
           bottom of the workspace, so a duplicate in the chat
           header would be redundant (and the variation summary
           overflows badly in this narrow slot anyway). On compact
           StatusBar isn't rendered, so this is the user's primary
           access point for changing model. -->
      <div v-if="isCompact && props.instance?.id && !readOnly" class="flex items-center px-2 py-1 -mb-px chat-model-switcher">
        <ModelSwitcher :instance-id="props.instance.id" />
      </div>

      <!-- Token usage + session info for active tab. The model name
           text remains for non-compact contexts (where the
           StatusBar handles model switching) and for read-only
           viewers (no instance id). -->
      <div v-if="activeTokens > 0 || (!isCompact && chat.modelDisplay) || (!props.instance?.id && chat.modelDisplay) || readOnly" class="flex items-center gap-2 px-2 py-2 -mb-px text-[10px] text-warm-400 font-mono">
        <template v-if="(!isCompact || !props.instance?.id || readOnly) && chat.modelDisplay">
          <span class="text-warm-500 dark:text-warm-400">{{ chat.modelDisplay }}</span>
          <span v-if="activeTokens > 0" class="text-warm-300 dark:text-warm-600">|</span>
        </template>
        <template v-if="activeTokens > 0">
          <span class="i-carbon-meter text-amber" />
          <span :title="t('chat.cumulativeInputTokens')">{{ t("common.in") }}: {{ formatTokens(activeUsage.prompt) }}</span>
          <span v-if="activeUsage.cached > 0" class="text-aquamarine" :title="t('chat.cachedInputTokens')">(cache {{ formatTokens(activeUsage.cached) }})</span>
          <span :title="t('chat.cumulativeOutputTokens')">{{ t("common.out") }}: {{ formatTokens(activeUsage.completion) }}</span>
        </template>
        <template v-if="chat.sessionInfo.compactThreshold > 0 && activeUsage.prompt > 0">
          <span class="text-warm-300 dark:text-warm-600">|</span>
          <span :class="contextPct >= 80 ? 'text-coral' : contextPct >= 60 ? 'text-amber' : ''" :title="t('chat.contextTitle', { current: formatTokens(activeUsage.lastPrompt || 0), limit: formatTokens(chat.sessionInfo.compactThreshold) })">{{ t("common.context") }}: {{ contextPct }}%</span>
        </template>
      </div>

      <!-- Tab bar bottom border (bubble top border) -->
      <div class="flex-1 border-b border-b-warm-200 dark:border-b-warm-700" />
    </div>

    <!-- Chat bubble: surface-level bg, equal margin left/right/bottom -->
    <div class="flex-1 mx-4 mb-4 bg-white dark:bg-warm-900 rounded-b-xl rounded-tr-xl border border-warm-200 dark:border-warm-700 border-t-0 overflow-hidden flex flex-col shadow-sm relative" :class="{ 'ring-2 ring-iolite/40 ring-inset': dragOver }" @dragenter.prevent="onDragEnter" @dragleave.prevent="onDragLeave" @dragover.prevent @drop.prevent="onDrop">
      <!-- Decorative top accent: subtle gem gradient -->
      <div class="h-0.5 w-full bg-gradient-to-r from-iolite/30 via-taaffeite/20 to-aquamarine/30" />

      <!-- Reconnect banner: surface when WS is attempting to reconnect -->
      <div v-if="chat.wsStatus === 'reconnecting'" class="flex items-center gap-2 px-4 py-1.5 text-xs bg-amber/10 dark:bg-amber/12 border-b border-amber/25 text-amber-shadow dark:text-amber-light">
        <span class="i-carbon-renew kohaku-pulse shrink-0" />
        <span>{{ t("chat.disconnected") }}</span>
      </div>

      <!-- Drag-over hint -->
      <div v-if="dragOver && !readOnly" class="absolute inset-0 z-10 flex items-center justify-center bg-iolite/5 dark:bg-iolite/10 backdrop-blur-sm pointer-events-none">
        <div class="px-4 py-2 rounded-lg bg-white dark:bg-warm-900 border border-iolite/40 shadow-lg text-sm text-iolite dark:text-iolite-light font-medium"><span class="i-carbon-upload mr-1" /> {{ t("chat.dropToAttach") }}</div>
      </div>

      <!-- Messages -->
      <div ref="messagesEl" class="chat-messages-viewport flex-1 overflow-y-auto px-5 py-4" @scroll="onMessagesScroll">
        <div class="flex flex-col gap-3">
          <template v-if="chat.currentMessages.length === 0">
            <div class="text-center py-16">
              <div class="w-12 h-12 rounded-2xl bg-gradient-to-br from-iolite/10 to-amber/10 dark:from-iolite/5 dark:to-amber/5 flex items-center justify-center mx-auto mb-3">
                <div class="i-carbon-chat text-xl text-iolite/40 dark:text-iolite-light/30" />
              </div>
              <p class="text-warm-400 dark:text-warm-500 text-sm">{{ resolvedEmptyTitle }}</p>
              <p class="text-warm-300 dark:text-warm-600 text-xs mt-1">{{ resolvedEmptySubtitle }}</p>
            </div>
          </template>
          <ChatMessage v-for="(msg, idx) in chat.currentMessages" :key="msg.id" :message="msg" :prev-message="idx > 0 ? chat.currentMessages[idx - 1] : null" :is-first="idx === 0" :message-idx="idx" :is-last-assistant="msg.role === 'assistant' && idx === chat.currentMessages.length - 1" />
          <div v-if="chat.processing" class="flex items-center gap-2.5 py-2 pl-1">
            <span class="w-2 h-2 rounded-full bg-amber kohaku-pulse" />
            <span class="text-sm text-amber/80 kohaku-pulse">{{ t("chat.processing") }}</span>
          </div>
        </div>
      </div>

      <!-- Queued messages: shown above input, not in main chat.
           Capped to QUEUE_VISIBLE items; overflow collapses into a "+N more"
           toggle so the input doesn't get pushed off-screen. -->
      <div v-if="!readOnly && chat.queuedMessages.length" class="px-4 pt-2 flex flex-col gap-1.5">
        <div v-for="qm in visibleQueued" :key="qm.id" class="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-amber/5 dark:bg-amber/5 border border-amber/20 text-sm">
          <span class="i-carbon-time text-amber/60 text-xs flex-shrink-0" />
          <span class="text-warm-500 dark:text-warm-400 truncate">{{ qm.content }}</span>
          <span class="text-warm-300 dark:text-warm-600 text-xs flex-shrink-0 ml-auto">{{ t("chat.queued") }}</span>
        </div>
        <button v-if="hiddenQueuedCount > 0" class="self-start text-xs text-amber-shadow dark:text-amber-light hover:underline" @click="queueExpanded = !queueExpanded">
          {{ queueExpanded ? t("chat.queueCollapse") : t("chat.queueShowMore", { count: hiddenQueuedCount }) }}
        </button>
      </div>

      <!-- Input: sits inside bubble, with subtle top border -->
      <div v-if="!readOnly" class="px-4 pb-4 pt-2 border-t border-t-warm-100 dark:border-t-warm-800">
        <!-- Pending UI events banner: shown when the user starts typing
             with one or more interactive bus events still awaiting a
             reply. Acts as a soft nudge — clicking scrolls to the most
             recent unreplied event. -->
        <div v-if="showPendingBanner" class="mb-2 flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-amber/10 dark:bg-amber/15 border border-amber/30 text-xs">
          <span class="i-carbon-warning-alt text-amber" />
          <span class="text-amber-shadow dark:text-amber-light">
            {{ pendingCount === 1 ? "1 pending request needs your reply" : `${pendingCount} pending requests need your reply` }}
          </span>
          <button class="ml-auto text-amber hover:underline" @click="scrollToPending">show</button>
        </div>
        <div v-if="attachments.length" class="mb-2 flex flex-wrap gap-2">
          <div v-for="(file, idx) in attachments" :key="file.name + ':' + idx" class="flex items-center gap-2 px-2.5 py-1 rounded-lg bg-iolite/8 dark:bg-iolite/12 border border-iolite/20 text-xs">
            <span :class="file.kind === 'image' ? 'i-carbon-image text-iolite dark:text-iolite-light' : 'i-carbon-document text-aquamarine'" />
            <span class="text-warm-700 dark:text-warm-200 max-w-40 truncate">{{ file.name }}</span>
            <button class="text-warm-400 hover:text-coral" @click="removeAttachment(idx)">
              <span class="i-carbon-close" />
            </button>
          </div>
        </div>
        <div class="flex gap-2 pl-2 pr-3 py-2 rounded-xl bg-warm-50 dark:bg-warm-800 border border-warm-200 dark:border-warm-700 focus-within:border-iolite/40 dark:focus-within:border-iolite-light/30 transition-colors items-end">
          <input ref="imageInputEl" type="file" accept="image/*" class="hidden" @change="(e) => onFileChange(e, 'image')" />
          <input ref="fileInputEl" type="file" class="hidden" @change="(e) => onFileChange(e, 'file')" />
          <div class="flex items-center gap-0 shrink-0 mb-0.5">
            <button class="w-7 h-7 flex items-center justify-center rounded-md transition-colors shrink-0 text-warm-400 hover:text-aquamarine dark:hover:text-aquamarine hover:bg-aquamarine/10" title="Attach file" aria-label="Attach file" @click="fileInputEl?.click()">
              <span class="i-carbon-add text-xs" />
            </button>
            <button class="w-7 h-7 flex items-center justify-center rounded-md transition-colors shrink-0 text-warm-400 hover:text-iolite dark:hover:text-iolite-light hover:bg-iolite/10" :title="t('chat.attachImage')" :aria-label="t('chat.attachImage')" @click="imageInputEl?.click()">
              <span class="i-carbon-image text-xs" />
            </button>
          </div>
          <textarea ref="inputEl" v-model="inputText" rows="1" class="flex-1 bg-transparent border-none outline-none text-sm text-warm-800 dark:text-warm-200 placeholder-warm-400 dark:placeholder-warm-500 resize-none max-h-32 leading-relaxed py-1 min-w-0" style="min-height: 2em" :placeholder="inputPlaceholder" @keydown="onInputKeydown" @input="autoResize" />
          <div class="flex items-center gap-1 shrink-0 mb-0.5">
            <button class="w-7 h-7 flex items-center justify-center rounded-md transition-colors text-warm-400 hover:text-iolite dark:hover:text-iolite-light hover:bg-iolite/10" :title="t('chat.compactContext')" :aria-label="t('chat.compactContext')" @click="triggerCompact">
              <span class="i-carbon-collapse-all text-xs" />
            </button>
            <button class="w-7 h-7 flex items-center justify-center rounded-md transition-colors text-warm-400 hover:text-coral hover:bg-coral/10" :title="t('chat.clearContext')" :aria-label="t('chat.clearContext')" @click="triggerClear">
              <span class="i-carbon-clean text-xs" />
            </button>
            <button v-if="chat.processing || chat.hasRunningJobs" class="w-8 h-8 flex items-center justify-center rounded-lg transition-all bg-coral/90 text-white hover:bg-coral shadow-sm shadow-coral/20" :title="`${t('chat.stopGeneration')} (Esc)`" :aria-label="t('chat.stopGeneration')" @click="chat.interrupt()">
              <span class="i-carbon-stop-filled text-sm" />
            </button>
            <button v-else class="w-8 h-8 flex items-center justify-center rounded-lg transition-all" :class="inputCanSend ? 'bg-iolite text-white hover:bg-iolite-shadow shadow-sm shadow-iolite/20' : 'text-warm-300 dark:text-warm-600 cursor-not-allowed'" :disabled="!inputCanSend" :aria-label="t('chat.sendMessage')" @click="send">
              <span class="i-carbon-send text-sm" />
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ElMessage, ElMessageBox } from "element-plus"

import StatusDot from "@/components/common/StatusDot.vue"
import ChatMessage from "@/components/chat/ChatMessage.vue"
import ModelSwitcher from "@/components/chrome/ModelSwitcher.vue"
import { useDensity } from "@/composables/useDensity"
import { useChatStore } from "@/stores/chat"
import { useI18n } from "@/utils/i18n"
import { terrariumAPI, agentAPI } from "@/utils/api"
import { buildMessageParts, formatBytes, MAX_ATTACHMENT_BYTES, MAX_IMAGE_BYTES } from "@/utils/chatAttachments"
import { getHybridPref, removeHybridPref, setHybridPref } from "@/utils/uiPrefs"
// How many queued-while-processing messages to show before collapsing.
const QUEUE_VISIBLE = 5

const props = defineProps({
  instance: { type: Object, required: true },
  readOnly: { type: Boolean, default: false },
  emptyTitle: { type: String, default: "" },
  emptySubtitle: { type: String, default: "" },
})

const chat = useChatStore()
const { t } = useI18n()
// Compact density renders the chat header model pill (since the
// StatusBar — which has its own ModelSwitcher — is hidden in the
// compact shell). Regular/expansive density already shows the
// switcher in StatusBar so this header-mounted copy is redundant.
const { isCompact } = useDensity()
const inputText = ref("")
const messagesEl = ref(null)
const inputEl = ref(null)
const imageInputEl = ref(null)
const fileInputEl = ref(null)
const attachments = ref([])
const queueExpanded = ref(false)
const dragOver = ref(false)
let dragDepth = 0

const visibleQueued = computed(() => {
  const queue = chat.queuedMessages
  if (queueExpanded.value || queue.length <= QUEUE_VISIBLE) return queue
  return queue.slice(0, QUEUE_VISIBLE)
})
const hiddenQueuedCount = computed(() => Math.max(0, chat.queuedMessages.length - QUEUE_VISIBLE))

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
const inputCanSend = computed(() => inputText.value.trim() || attachments.value.length > 0)

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
  if (!chat.activeTab) return t("chat.selectTab")
  if (chat.activeTab.startsWith("ch:")) return t("chat.sendToChannel", { channel: chat.activeTab.slice(3) })
  return t("chat.messagePlaceholder")
})

const resolvedEmptyTitle = computed(() => props.emptyTitle || t("chat.noMessagesYet"))
const resolvedEmptySubtitle = computed(() => props.emptySubtitle || t("chat.getStarted"))

// Phase B: count interactive bus events that haven't been replied to
// or superseded yet, scoped to the active tab. Banner appears when
// the user starts typing while there are pending requests.
const pendingCount = computed(() => {
  const tab = chat.activeTab
  if (!tab) return 0
  const list = chat.messagesByTab?.[tab] || []
  return list.filter((m) => m.role === "ui_event" && m.interactive && !m.replied && !m.superseded && !m.timedOut).length
})

const showPendingBanner = computed(() => pendingCount.value > 0 && inputText.value.length > 0)

function scrollToPending() {
  const tab = chat.activeTab
  if (!tab) return
  const list = chat.messagesByTab?.[tab] || []
  const target = list.filter((m) => m.role === "ui_event" && m.interactive && !m.replied && !m.superseded && !m.timedOut).pop()
  if (!target) return
  const el = messagesEl.value
  if (!el) return
  // Find the rendered message by id; ChatMessage components don't
  // expose an explicit id attribute, so we use querySelector by
  // ``data-message-id`` if present, falling back to scrolling to the
  // bottom of the list.
  const node = el.querySelector(`[data-message-id="${target.id}"]`)
  if (node && typeof node.scrollIntoView === "function") {
    node.scrollIntoView({ behavior: "smooth", block: "center" })
  } else {
    el.scrollTop = el.scrollHeight
  }
}

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
  const contentLen = typeof last.content === "string" ? last.content.length : Array.isArray(last.content) ? last.content.length : 0
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
  ([instanceId, tab], previous) => {
    const [prevInstanceId, prevTab] = previous || []
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

function _pushAttachment(file, kind) {
  const limit = kind === "image" ? MAX_IMAGE_BYTES : MAX_ATTACHMENT_BYTES
  if (file.size > limit) {
    ElMessage.error(
      t("chat.attachmentTooLarge", {
        name: file.name,
        size: formatBytes(file.size),
        limit: formatBytes(limit),
      }),
    )
    return false
  }
  if (kind === "image" && file.type && !file.type.startsWith("image/")) {
    ElMessage.error(t("chat.attachmentNotImage", { name: file.name }))
    return false
  }
  attachments.value.push({ file, name: file.name, kind })
  return true
}

async function onFileChange(e, kind = "file") {
  const files = Array.from(e.target.files || [])
  for (const file of files) _pushAttachment(file, kind)
  e.target.value = ""
}

// ── Drag-and-drop: routes dropped files through the same validation. ──
function onDragEnter(e) {
  if (props.readOnly) return
  if (!e.dataTransfer || !Array.from(e.dataTransfer.types).includes("Files")) return
  dragDepth++
  dragOver.value = true
}
function onDragLeave() {
  if (props.readOnly) return
  dragDepth = Math.max(0, dragDepth - 1)
  if (dragDepth === 0) dragOver.value = false
}
function onDrop(e) {
  dragDepth = 0
  dragOver.value = false
  if (props.readOnly) return
  const files = Array.from(e.dataTransfer?.files || [])
  for (const file of files) {
    const kind = file.type.startsWith("image/") ? "image" : "file"
    _pushAttachment(file, kind)
  }
}

function removeAttachment(index) {
  attachments.value.splice(index, 1)
}

async function send() {
  if (props.readOnly || (!inputText.value.trim() && attachments.value.length === 0)) return
  const parts = await buildMessageParts(inputText.value, attachments.value)
  chat.send(parts)
  inputText.value = ""
  attachments.value = []
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
    const sid = chat._instanceGraphId || chat._instanceId
    const tab = chat.activeTab || "root"
    const response = await terrariumAPI.executeCreatureCommand(sid, tab, "compact")
    // ``/compact`` returns a ``ui_notify`` payload describing one of
    // four outcomes: triggered, no-controller, too-short, busy. Without
    // surfacing it the user has no signal that the click did anything
    // — the compact runs (or doesn't) silently in the background.
    surfaceCommandResult(response)
  } catch (err) {
    console.error("Compact failed:", err)
    ElMessage.error(`Compact failed: ${err?.message || err}`)
  }
}

/**
 * Render a ``UserCommandResult`` payload as a toast / inline message.
 *
 * Backend command results carry a ``data`` block built by ``ui_notify``
 * (and friends) in ``modules/user_command/base.py``. CLI/TUI commit
 * ``output`` to their own surfaces; the web frontend is responsible
 * for translating the typed payload into UI. This helper covers the
 * "notify" case — additional types (``select``, ``confirm``, …) get
 * wired up when the command needing them surfaces in the chat header.
 */
function surfaceCommandResult(response) {
  if (!response) return
  if (response.error) {
    ElMessage.error(response.error)
    return
  }
  const payload = response.data
  if (payload && payload.type === "notify" && payload.message) {
    const level = payload.level || "info"
    const fn = ElMessage[level] || ElMessage.info
    fn(payload.message)
    return
  }
  // Fall back to plain ``output`` text when no structured payload —
  // mirrors how CLI / TUI render unspecified results.
  if (response.output) {
    ElMessage({ message: response.output, type: "info" })
  }
}

async function triggerClear() {
  if (props.readOnly) return
  try {
    await ElMessageBox.confirm(t("chat.clearConfirm"), t("chat.clearContext"), {
      type: "warning",
      confirmButtonText: t("common.clear"),
      cancelButtonText: t("common.cancel"),
    })
  } catch {
    return // user cancelled
  }
  try {
    const sid = chat._instanceGraphId || chat._instanceId
    const tab = chat.activeTab || "root"
    const response = await terrariumAPI.executeCreatureCommand(sid, tab, "clear", "--force")
    surfaceCommandResult(response)
  } catch (err) {
    console.error("Clear failed:", err)
    ElMessage.error(`Clear failed: ${err?.message || err}`)
  }
}

async function stopTask(jobId, jobName) {
  try {
    const tab = chat.activeTab
    const sid = chat._instanceGraphId || chat._instanceId
    await terrariumAPI.stopCreatureTask(sid, tab || "root", jobId)
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

<style scoped>
.chat-messages-viewport {
  container-type: size;
}

/* ModelSwitcher's pill defaults to min-width: 12rem which is too
   wide for the chat tab-bar header on compact viewports. Shrink
   here without touching the global StatusBar usage. The variation
   summary is hidden in this context — it overflows badly in the
   narrow slot, and the user can still see/change variations from
   the picker popover itself. */
.chat-model-switcher :deep(.model-pill) {
  min-width: 0;
  max-width: 14rem;
  padding: 0.15rem 0.45rem;
  min-height: 24px;
  gap: 0.35rem;
}
.chat-model-switcher :deep(.model-pill-variation) {
  display: none;
}
.chat-model-switcher :deep(.target-select) {
  width: 7rem;
}
</style>
