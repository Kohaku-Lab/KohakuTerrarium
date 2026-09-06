<template>
  <div class="h-full flex flex-col bg-warm-100 dark:bg-[#211F1D]" :class="showFocusRing ? 'ring-1 ring-inset ring-iolite/40 dark:ring-iolite-light/30' : ''" @focusin="onGroupFocus" @mousedown="onGroupFocus">
    <div role="tablist" class="flex items-end gap-0 px-4 pt-2 shrink-0 min-w-0">
      <div class="flex items-end overflow-x-auto scrollbar-none min-w-0">
        <div v-for="tab in viewTabs" :key="tab" role="tab" tabindex="0" :draggable="!!props.groupId" :aria-selected="viewActiveTab === tab" class="relative flex items-center gap-1.5 px-3.5 py-2 text-xs font-medium cursor-pointer select-none rounded-t-lg -mb-px transition-colors shrink-0" :class="viewActiveTab === tab ? 'bg-white dark:bg-warm-900 text-warm-800 dark:text-warm-200 border border-warm-200 dark:border-warm-700 border-b-white dark:border-b-warm-900 z-10' : 'text-warm-400 dark:text-warm-500 hover:text-warm-600 dark:hover:text-warm-400 border border-transparent'" @click="onTabClick(tab)" @keydown.enter="onTabClick(tab)" @keydown.space.prevent="onTabClick(tab)" @dragstart="onTabDragStart($event, tab)" @dragend="onTabDragEnd" @dragover.prevent="onTabStripDragOver($event)" @drop.prevent.stop="onTabStripDrop($event, viewTabs.indexOf(tab))">
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
            <SiteChip :node-id="getCreatureHomeNode(tab)" />
          </template>

          <button v-if="tab !== 'root' && (viewTabs.length > 1 || multipleGroupsExist)" class="ml-1 w-7 h-7 sm:w-4 sm:h-4 flex items-center justify-center rounded-sm text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors" :aria-label="t('chat.closeTab', { tab })" @click.stop="closeTab(tab)">
            <div class="i-carbon-close text-sm sm:text-[10px]" />
          </button>
        </div>
      </div>

      <div v-if="isCompact && props.instance?.id && !readOnly" class="flex items-center px-2 py-1 -mb-px chat-model-switcher">
        <ModelSwitcher :instance-id="props.instance.id" />
      </div>

      <div v-if="activeTokens > 0 || (!isCompact && viewModelDisplay) || (!props.instance?.id && viewModelDisplay) || readOnly" class="flex items-center gap-2 px-2 py-2 -mb-px text-[10px] text-warm-400 font-mono">
        <template v-if="(!isCompact || !props.instance?.id || readOnly) && viewModelDisplay">
          <span class="text-warm-500 dark:text-warm-400">{{ viewModelDisplay }}</span>
          <span v-if="activeTokens > 0" class="text-warm-300 dark:text-warm-600">|</span>
        </template>
        <template v-if="activeTokens > 0">
          <span class="i-carbon-meter text-amber" />
          <span :title="t('chat.cumulativeInputTokens')">{{ t("common.in") }}: {{ formatTokens(activeUsage.prompt) }}</span>
          <span v-if="activeUsage.cached > 0" class="text-aquamarine" :title="t('chat.cachedInputTokens')">(cache {{ formatTokens(activeUsage.cached) }})</span>
          <span :title="t('chat.cumulativeOutputTokens')">{{ t("common.out") }}: {{ formatTokens(activeUsage.completion) }}</span>
        </template>
        <template v-if="viewModelInfo.compactThreshold > 0 && activeUsage.prompt > 0">
          <span class="text-warm-300 dark:text-warm-600">|</span>
          <span :class="contextPct >= 80 ? 'text-coral' : contextPct >= 60 ? 'text-amber' : ''" :title="t('chat.contextTitle', { current: formatTokens(activeUsage.lastPrompt || 0), limit: formatTokens(viewModelInfo.compactThreshold) })">{{ t("common.context") }}: {{ contextPct }}%</span>
        </template>
      </div>

      <div class="flex-1 border-b border-b-warm-200 dark:border-b-warm-700" />
    </div>

    <div ref="bubbleEl" class="flex-1 mx-4 mb-4 bg-white dark:bg-warm-900 rounded-b-xl rounded-tr-xl border border-warm-200 dark:border-warm-700 border-t-0 overflow-hidden flex flex-col shadow-sm relative" :class="{ 'ring-2 ring-iolite/40 ring-inset': dragOver }" @dragenter="onBubbleDragEnter" @dragleave="onBubbleDragLeave" @dragover="onBubbleDragOver" @drop="onBubbleDrop">
      <template v-if="props.groupId && tabDragHoverEdge">
        <div v-if="tabDragHoverEdge === 'left'" class="absolute inset-y-0 left-0 w-1/4 bg-iolite/15 dark:bg-iolite-light/12 border-r-2 border-iolite/50 pointer-events-none z-20" />
        <div v-if="tabDragHoverEdge === 'right'" class="absolute inset-y-0 right-0 w-1/4 bg-iolite/15 dark:bg-iolite-light/12 border-l-2 border-iolite/50 pointer-events-none z-20" />
        <div v-if="tabDragHoverEdge === 'top'" class="absolute inset-x-0 top-0 h-1/4 bg-iolite/15 dark:bg-iolite-light/12 border-b-2 border-iolite/50 pointer-events-none z-20" />
        <div v-if="tabDragHoverEdge === 'bottom'" class="absolute inset-x-0 bottom-0 h-1/4 bg-iolite/15 dark:bg-iolite-light/12 border-t-2 border-iolite/50 pointer-events-none z-20" />
        <div v-if="tabDragHoverEdge === 'center'" class="absolute inset-0 bg-iolite/8 dark:bg-iolite-light/8 border-2 border-iolite/40 rounded pointer-events-none z-20" />
      </template>
      <div class="h-0.5 w-full bg-gradient-to-r from-iolite/30 via-taaffeite/20 to-aquamarine/30" />

      <div v-if="chat.wsStatus === 'reconnecting'" class="flex items-center gap-2 px-4 py-1.5 text-xs bg-amber/10 dark:bg-amber/12 border-b border-amber/25 text-amber-shadow dark:text-amber-light">
        <span class="i-carbon-renew kohaku-pulse shrink-0" />
        <span>{{ t("chat.disconnected") }}</span>
      </div>

      <div v-if="dragOver && !readOnly" class="absolute inset-0 z-10 flex items-center justify-center bg-iolite/5 dark:bg-iolite/10 backdrop-blur-sm pointer-events-none">
        <div class="px-4 py-2 rounded-lg bg-white dark:bg-warm-900 border border-iolite/40 shadow-lg text-sm text-iolite dark:text-iolite-light font-medium"><span class="i-carbon-upload mr-1" /> {{ t("chat.dropToAttach") }}</div>
      </div>

      <ChatTranscriptSection :messages="windowMessages" :message-offset="windowStart" :total-count="viewMessages.length" :previous-message="windowStart > 0 ? viewMessages[windowStart - 1] : null" :empty-title="resolvedEmptyTitle" :empty-subtitle="resolvedEmptySubtitle" :processing="showKohakUwUingIndicator" :processing-label="kohakuwuingLabel" :reconnecting="chat.wsStatus === 'reconnecting'" :reconnect-label="t('chat.disconnected')" :earlier-count="windowStart" :earlier-label="t('chat.showEarlier', { count: windowStart })" :render-message="renderTranscriptMessage" @load-earlier="loadEarlierMessages" @scroll="onMessagesScroll" @viewport-ready="onTranscriptViewportReady" />

      <div v-if="!readOnly && activeQueue.length" class="px-4 pt-2 flex flex-col gap-1.5">
        <div v-for="qm in visibleQueued" :key="qm.id" class="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-amber/5 dark:bg-amber/5 border border-amber/20 text-sm" :class="{ 'opacity-50': qm.cancelling }">
          <span class="i-carbon-time text-amber/60 text-xs flex-shrink-0" />
          <template v-if="editingQueueId === qm.eventId">
            <input v-model="editQueueText" class="flex-1 min-w-0 bg-transparent border border-amber/30 rounded px-2 py-0.5 text-sm focus:outline-none focus:border-amber" @keydown.enter.prevent="saveEditQueue(qm)" @keydown.esc="cancelEditQueue" />
            <button class="text-xs text-iolite hover:underline flex-shrink-0" @click="saveEditQueue(qm)">{{ t("common.save") }}</button>
            <button class="text-xs text-warm-400 hover:underline flex-shrink-0" @click="cancelEditQueue">{{ t("common.cancel") }}</button>
          </template>
          <template v-else>
            <span class="text-warm-500 dark:text-warm-400 truncate flex-1">{{ qm.content }}</span>
            <span class="text-warm-300 dark:text-warm-600 text-xs flex-shrink-0">{{ t("chat.queued") }}</span>
            <button class="i-carbon-edit text-warm-400 hover:text-iolite text-sm flex-shrink-0" :title="t('chat.queueEdit')" :disabled="qm.cancelling" @click="startEditQueue(qm)" />
            <button class="i-carbon-close text-warm-400 hover:text-coral text-sm flex-shrink-0" :title="t('chat.queueCancel')" :disabled="qm.cancelling" @click="chat.cancelQueuedMessage(viewActiveTab, qm.eventId)" />
          </template>
        </div>
        <button v-if="hiddenQueuedCount > 0" class="self-start text-xs text-amber-shadow dark:text-amber-light hover:underline" @click="queueExpanded = !queueExpanded">
          {{ queueExpanded ? t("chat.queueCollapse") : t("chat.queueShowMore", { count: hiddenQueuedCount }) }}
        </button>
      </div>

      <div v-if="!readOnly" class="px-4 pb-4 pt-2 border-t border-t-warm-100 dark:border-t-warm-800">
        <div v-if="showPendingBanner" class="mb-2 flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-amber/10 dark:bg-amber/15 border border-amber/30 text-xs">
          <span class="i-carbon-warning-alt text-amber" />
          <span class="text-amber-shadow dark:text-amber-light">{{ t("chat.pendingBanner", { count: pendingCount }) }}</span>
          <button class="ml-auto text-amber hover:underline" @click="scrollToPending">{{ t("chat.pendingShow") }}</button>
        </div>
        <ChatComposer ref="composerEl" v-model="inputText" v-model:attachments="attachments" :processing="viewProcessing" :compact-mode="isCompact" :managed-submit="true" :max-attachment-bytes="MAX_ATTACHMENT_BYTES" :max-image-bytes="MAX_IMAGE_BYTES" :placeholder="inputPlaceholder" :labels="composerLabels" aria-autocomplete="list" :aria-expanded="slashMenuOpen" aria-controls="slash-command-menu" :aria-activedescendant="slashActiveDescendant" input-role="combobox" :attachment-transform="transformAttachment" @update:attachments="onComposerAttachmentsChanged" @submit="send" @interrupt="chat.interrupt(viewActiveTab)" @compact="triggerCompact" @clear="triggerClear" @error="onAttachmentError" @input="onInputChanged" @keydown="onInputKeydown" @focus="onInputFocus" @blur="onInputBlur" @drag-state="dragOver = $event">
          <template #suggestions><SlashCommandMenu :open="slashMenuOpen" :loading="slashInventoryLoading" :entries="slashMatches" :selected-index="slashSelectedIndex" @choose="chooseSlashEntry" @select-index="slashSelectedIndex = $event" /></template>
          <template #attachment-icon="{ attachment }"><span :class="attachment.kind === 'image' ? 'i-carbon-image text-iolite dark:text-iolite-light' : 'i-carbon-document text-aquamarine'" /></template>
          <template #remove-icon><span class="i-carbon-close" /></template>
          <template #file-icon><span class="i-carbon-add" /></template>
          <template #image-icon><span class="i-carbon-image" /></template>
          <template #more-icon><span class="i-carbon-add" /></template>
          <template #compact-icon><span class="i-carbon-collapse-all" /></template>
          <template #clear-icon><span class="i-carbon-clean" /></template>
          <template #stop-icon><span class="i-carbon-stop-filled" /></template>
          <template #send-icon><span class="i-carbon-send" /></template>
        </ChatComposer>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ElMessage, ElMessageBox } from "element-plus"

import { h, inject, provide } from "vue"

import StatusDot from "@/components/common/StatusDot.vue"
import ChatMessage from "@/components/chat/ChatMessage.vue"
import { ChatComposer, ChatTranscriptSection } from "@kohakuterrarium/chat-ui"
import { useChatRenderWindow, CHAT_RENDER_EXPAND_MESSAGE_LIMIT, CHAT_RENDER_EXPAND_UNIT_BUDGET } from "@/components/chat/chatRenderWindow"
import { createChatHistoryExpander, captureViewportAnchor, restoreViewportAnchor } from "@/components/chat/chatHistoryExpand"
import { createChatScrollScheduler } from "@/components/chat/chatScrollScheduler"
import SlashCommandMenu from "@/components/chat/SlashCommandMenu.vue"
import ModelSwitcher from "@/components/chrome/ModelSwitcher.vue"
import SiteChip from "@/components/cluster/SiteChip.vue"
import { useDensity } from "@/composables/useDensity"
import { useSlashCommandCompletion } from "@/composables/useSlashCommandCompletion"
import { _parseSlashCommand, useChatStore } from "@/stores/chat"
import { useChatTabDrag } from "@/composables/useChatTabDrag"
import { useI18n } from "@/utils/i18n"
import { terrariumAPI, agentAPI } from "@/utils/api"
import { buildMessageParts, formatBytes, MAX_ATTACHMENT_BYTES, MAX_IMAGE_BYTES } from "@/utils/chatAttachments"
import { readLocalPref, writeLocalPref } from "@/utils/uiPrefs"
import { shouldSendOnEnter } from "@/utils/chatInput"
const QUEUE_VISIBLE = 5

const props = defineProps({
  instance: { type: Object, required: true },
  readOnly: { type: Boolean, default: false },
  emptyTitle: { type: String, default: "" },
  emptySubtitle: { type: String, default: "" },
  groupId: { type: String, default: null },
})

const emit = defineEmits(["focus-group"])

const injectedChat = inject("chatStore", null)
const chat = injectedChat || useChatStore(props.instance?.id || props.instance?.graph_id || undefined)
provide("chatStore", chat)
const { t } = useI18n()
const { isCompact } = useDensity()
const inputText = ref("")
const composerRevision = ref(0)
const submitInFlight = ref(false)
const messagesEl = ref(null)
const composerEl = ref(null)
const bubbleEl = ref(null)
const attachments = ref([])
const queueExpanded = ref(false)
const dragOver = ref(false)
let fileDragDepth = 0

const viewGroup = computed(() => (props.groupId ? chat.groups?.[props.groupId] || null : null))
const viewTabs = computed(() => (viewGroup.value ? viewGroup.value.tabs : chat.tabs))
const viewActiveTab = computed(() => (viewGroup.value ? viewGroup.value.activeTab : chat.activeTab))
const viewInstanceId = computed(() => props.instance?.id || chat._instanceId || null)
const scrollScope = computed(() => ({
  groupId: props.groupId,
  instanceId: viewInstanceId.value,
  tab: viewActiveTab.value,
}))
const viewMessages = computed(() => {
  const t = viewActiveTab.value
  return t ? chat.messagesByTab[t] || [] : []
})
const viewProcessing = computed(() => {
  const t = viewActiveTab.value
  return t ? !!chat.processingByTab[t] : false
})
const viewModelInfo = computed(() => {
  const t = viewActiveTab.value
  const info = (t && chat.modelByTab[t]) || {}
  return {
    model: info.model || chat.sessionInfo.model || "",
    llmName: info.llmName || chat.sessionInfo.llmName || "",
    maxContext: info.maxContext || chat.sessionInfo.maxContext || 0,
    compactThreshold: info.compactThreshold || chat.sessionInfo.compactThreshold || 0,
  }
})
const viewModelDisplay = computed(() => viewModelInfo.value.llmName || viewModelInfo.value.model || "")
const isFocusedGroup = computed(() => !!(props.groupId && chat.focusedGroupId === props.groupId))

const multipleGroupsExist = computed(() => Object.keys(chat.groups || {}).length > 1)

const showFocusRing = computed(() => isFocusedGroup.value && multipleGroupsExist.value)

function onTabClick(tab) {
  if (props.groupId) {
    chat.setGroupActiveTab(props.groupId, tab)
    chat.setFocusedGroup(props.groupId)
    emit("focus-group", props.groupId)
  } else {
    chat.setActiveTab(tab)
  }
}

function onGroupFocus() {
  if (!props.groupId) return
  if (chat.focusedGroupId !== props.groupId) {
    chat.setFocusedGroup(props.groupId)
  }
  emit("focus-group", props.groupId)
}

const { activeDescendant: slashActiveDescendant, choose: chooseSlashEntry, dismiss: dismissSlashMenu, entries: slashMatches, loading: slashInventoryLoading, move: moveSlashSelection, open: slashMenuOpen, reopen: reopenSlashMenu, selectedIndex: slashSelectedIndex } = useSlashCommandCompletion({ chat, inputText, activeTabKey: viewActiveTab })

const tabDrag = useChatTabDrag(chat)
const tabDragHoverEdge = computed(() => (props.groupId ? tabDrag.isHoveringEdgeOf(props.groupId) : null))

function onTabDragStart(ev, tab) {
  if (!props.groupId) return
  tabDrag.onTabDragStart(ev, props.groupId, tab)
}
function onTabDragEnd() {
  tabDrag.onTabDragEnd()
}
function onTabStripDragOver(ev) {
  if (!props.groupId) return
  tabDrag.onTabStripDragOver(ev, props.groupId)
}
function onTabStripDrop(ev, dstIndex) {
  if (!props.groupId) return
  tabDrag.onTabStripDrop(ev, props.groupId, dstIndex)
}
function hasDraggedFiles(ev) {
  return Array.from(ev.dataTransfer?.types || []).includes("Files")
}
function onBubbleDragEnter(ev) {
  if (props.readOnly || !hasDraggedFiles(ev)) return
  ev.preventDefault()
  fileDragDepth += 1
  dragOver.value = true
}
function onBubbleDragLeave(ev) {
  if (hasDraggedFiles(ev)) {
    fileDragDepth = Math.max(0, fileDragDepth - 1)
    if (!fileDragDepth) dragOver.value = false
  }
  if (props.groupId) tabDrag.onBubbleDragLeave(ev, props.groupId)
}
function onBubbleDragOver(ev) {
  const types = Array.from(ev.dataTransfer?.types || [])
  const isTabDrag = props.groupId && types.includes("application/x-kt-tab")
  if (isTabDrag) {
    tabDrag.onBubbleDragOver(ev, props.groupId)
    return
  }
  if (hasDraggedFiles(ev) || types.includes("text/uri-list") || types.includes("text/plain")) {
    ev.preventDefault()
  }
}

const activeQueue = computed(() => {
  const t = viewActiveTab.value
  return t ? chat.queuedMessagesByTab[t] || [] : []
})
const visibleQueued = computed(() => {
  const queue = activeQueue.value
  if (queueExpanded.value || queue.length <= QUEUE_VISIBLE) return queue
  return queue.slice(0, QUEUE_VISIBLE)
})
const hiddenQueuedCount = computed(() => Math.max(0, activeQueue.value.length - QUEUE_VISIBLE))

const editingQueueId = ref(null)
const editQueueText = ref("")
function startEditQueue(qm) {
  editingQueueId.value = qm.eventId
  editQueueText.value = qm.content || ""
}
function cancelEditQueue() {
  editingQueueId.value = null
  editQueueText.value = ""
}
function saveEditQueue(qm) {
  if (editQueueText.value.trim()) {
    chat.editQueuedMessage(viewActiveTab.value, qm.eventId, editQueueText.value)
  }
  cancelEditQueue()
}

function draftKey() {
  const instanceId = props.instance?.id || chat._instanceId || ""
  const tab = viewActiveTab.value || ""
  if (!instanceId || !tab || props.readOnly) return ""
  const suffix = props.groupId ? `.${props.groupId}` : ""
  return `kt.chat.draft.${instanceId}.${tab}${suffix}`
}

function restoreDraft() {
  const key = draftKey()
  if (!key) {
    inputText.value = ""
    return
  }
  inputText.value = readLocalPref(key) || ""
  nextTick(() => composerEl.value?.resize())
}

function persistDraft() {
  const key = draftKey()
  if (!key) return
  writeLocalPref(key, inputText.value || null)
}

const activeUsage = computed(() => {
  const tab = viewActiveTab.value
  if (!tab) return { prompt: 0, completion: 0, total: 0 }
  return chat.tokenUsage[tab] || { prompt: 0, completion: 0, total: 0 }
})

const activeTokens = computed(() => activeUsage.value.total)
const composerLabels = computed(() => ({
  attachFile: t("chat.attachFile"),
  attachImage: t("chat.attachImage"),
  clear: t("chat.clearContext"),
  compact: t("chat.compactContext"),
  message: inputPlaceholder.value,
  moreActions: t("chat.moreActions"),
  removeAttachment: "Remove {name}",
  send: t("chat.sendMessage"),
  stop: t("chat.stopGeneration"),
}))

const contextPct = computed(() => {
  const threshold = viewModelInfo.value.compactThreshold
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
  const tab = viewActiveTab.value
  if (!tab) return t("chat.selectTab")
  if (tab.startsWith("ch:")) return t("chat.sendToChannel", { channel: tab.slice(3) })
  return t("chat.messagePlaceholder")
})

const resolvedEmptyTitle = computed(() => props.emptyTitle || t("chat.noMessagesYet"))
const resolvedEmptySubtitle = computed(() => props.emptySubtitle || t("chat.getStarted"))

const pendingCount = computed(() => {
  const tab = viewActiveTab.value
  if (!tab) return 0
  const list = chat.messagesByTab?.[tab] || []
  return list.filter((m) => m.role === "ui_event" && m.interactive && !m.replied && !m.superseded && !m.timedOut).length
})

const showPendingBanner = computed(() => pendingCount.value > 0 && inputText.value.length > 0)

const viewRunningJobCount = computed(() => chat.runningJobCountForTab(viewActiveTab.value))

const showKohakUwUingIndicator = computed(() => {
  if (viewRunningJobCount.value > 0) return true
  if (!props.groupId || isFocusedGroup.value) {
    return chat.processing && chat.viewingRunningBranch
  }
  return viewProcessing.value
})

function onTranscriptViewportReady(viewport) {
  messagesEl.value = viewport
  viewport.classList.add("chat-messages-viewport")
}

function renderTranscriptMessage(message, context) {
  return h(ChatMessage, { message, prevMessage: context.previousMessage, isFirst: context.isFirst, messageIdx: context.absoluteIndex, isLastAssistant: context.isLastAssistant, tabId: viewActiveTab.value })
}

const kohakuwuingLabel = computed(() => {
  const streaming = !props.groupId || isFocusedGroup.value ? chat.processing && chat.viewingRunningBranch : viewProcessing.value
  const bgCount = viewRunningJobCount.value
  if (streaming && bgCount) return t("chat.processingStreamingBg", { n: bgCount })
  if (streaming) return t("chat.processingStreaming")
  if (bgCount) return t("chat.processingWaitingBg", { n: bgCount })
  return t("chat.processing")
})

async function scrollToPending() {
  const tab = viewActiveTab.value
  if (!tab) return
  const list = chat.messagesByTab?.[tab] || []
  const target = list.filter((m) => m.role === "ui_event" && m.interactive && !m.replied && !m.superseded && !m.timedOut).pop()
  if (!target) return
  const targetIdx = list.indexOf(target)
  scrollScheduler.suppress()
  isNearBottom.value = false
  if (targetIdx >= 0 && targetIdx < windowStart.value) {
    enterHistoryAt(targetIdx)
    await nextTick()
  }
  const el = messagesEl.value
  if (!el) return
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

function getCreatureHomeNode(name) {
  const creature = props.instance.creatures.find((c) => c.name === name)
  return creature?.home_node || props.instance?.home_node || "_host"
}

function closeTab(tab) {
  if (props.readOnly) return
  chat.closeTab(tab)
}

function onInputKeydown(e) {
  if (props.readOnly) return
  if (slashMenuOpen.value) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault()
      moveSlashSelection(e.key === "ArrowDown" ? 1 : -1)
      return
    }
    if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
      const selected = slashMatches.value[slashSelectedIndex.value]
      if (selected) {
        e.preventDefault()
        chooseSlashEntry(selected)
        return
      }
    }
    if (e.key === "Escape") {
      e.preventDefault()
      e.stopPropagation()
      dismissSlashMenu()
      return
    }
  }
  if (shouldSendOnEnter(e, { isCompact: isCompact.value })) {
    e.preventDefault()
    send()
  }
}

function onInputChanged() {
  composerRevision.value += 1
  chat.markSlashTarget(viewActiveTab.value, null)
}

function onInputFocus() {
  reopenSlashMenu()
}

function onInputBlur() {}

const isNearBottom = ref(true)
const forceScrollOnNextMessageUpdate = ref(true)
const scrollPositions = new Map()

function getScrollKey(instanceId = props.instance?.id || chat._instanceId, tab = viewActiveTab.value, groupId = props.groupId) {
  if (!instanceId || !tab) return ""
  const suffix = groupId ? `:${groupId}` : ""
  return `${instanceId}:${tab}${suffix}`
}

// Live tail is selected by an estimated render-unit budget. An explicit
// start marks history-reading mode: its top stays fixed while the open
// end keeps newly arriving messages reachable.
const { enterHistoryAt, expandHistory, isHistoryMode, leaveHistory, restoreHistory, windowMessages, windowStart } = useChatRenderWindow(viewMessages, () => getScrollKey())

// Continuous upward scrolling: reaching the top of the rendered window
// expands it one small step, and an idle lookahead pre-mounts the next
// step so back-to-back expansions never stall the scroll interaction.
const historyExpander = createChatHistoryExpander({
  canExpand: () => isHistoryMode.value && windowStart.value > 0,
  expand: () => expandHistory({ unitBudget: CHAT_RENDER_EXPAND_UNIT_BUDGET, messageLimit: CHAT_RENDER_EXPAND_MESSAGE_LIMIT }),
  getViewportEl: () => messagesEl.value,
  getContext: () => getScrollKey(),
})

async function loadEarlierMessages() {
  const anchor = captureViewportAnchor(() => messagesEl.value)
  expandHistory()
  await nextTick()
  restoreViewportAnchor(() => messagesEl.value, anchor)
}

let lastObservedScrollTop = 0
function updateNearBottom() {
  const el = messagesEl.value
  if (!el) return
  isNearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  lastObservedScrollTop = el.scrollTop
}

function saveScrollPosition(instanceId = props.instance?.id || chat._instanceId, tab = viewActiveTab.value, groupId = props.groupId) {
  const el = messagesEl.value
  const key = getScrollKey(instanceId, tab, groupId)
  if (!el || !key) return
  scrollPositions.set(key, el.scrollTop)
}

function restoreScrollPosition(instanceId = props.instance?.id || chat._instanceId, tab = viewActiveTab.value, groupId = props.groupId) {
  const el = messagesEl.value
  const key = getScrollKey(instanceId, tab, groupId)
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

let scrollStateFrame = null
function onMessagesScroll() {
  const el = messagesEl.value
  if (el && el.scrollTop < lastObservedScrollTop) {
    if (!isHistoryMode.value) enterHistoryAt(windowStart.value)
    isNearBottom.value = false
    scrollScheduler.suppress()
  }
  if (el) lastObservedScrollTop = el.scrollTop
  if (scrollStateFrame !== null) return
  scrollStateFrame = requestAnimationFrame(() => {
    scrollStateFrame = null
    updateNearBottom()
    if (isNearBottom.value) {
      leaveHistory()
      scrollScheduler.resume()
    } else if (el) {
      historyExpander.maybeExpandAtTop(el.scrollTop)
    }
    saveScrollPosition()
  })
}

function scrollToBottom() {
  leaveHistory()
  const el = messagesEl.value
  if (!el) return
  scrollScheduler.resume()
  el.scrollTop = el.scrollHeight
  updateNearBottom()
  saveScrollPosition()
}

const scrollScheduler = createChatScrollScheduler({
  afterDomCommit: nextTick,
  requestFrame: (callback) => requestAnimationFrame(callback),
  cancelFrame: (id) => cancelAnimationFrame(id),
  shouldScroll: () => isNearBottom.value,
  scroll: scrollToBottom,
})
const scheduleScrollToBottom = (force = false) => scrollScheduler.schedule(force, scrollScope.value)

const messageTailSignature = computed(() => {
  const messages = viewMessages.value
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

watch(
  () => [scrollScope.value, messageTailSignature.value],
  ([scope, nextSig], previous) => {
    const [previousScope, prevSig] = previous || []
    if (scope !== previousScope || !prevSig || nextSig === prevSig) return
    const force = forceScrollOnNextMessageUpdate.value
    forceScrollOnNextMessageUpdate.value = false
    scheduleScrollToBottom(force)
  },
)

watch(
  () => [scrollScope.value, viewProcessing.value],
  ([scope, val], previous) => {
    if (scope === previous?.[0] && val) scheduleScrollToBottom()
  },
)

watch(
  scrollScope,
  (scope, previousScope) => {
    scrollScheduler.invalidate()
    scrollScheduler.resume()
    historyExpander.cancelIdleExpand()
    if (scrollStateFrame !== null) {
      cancelAnimationFrame(scrollStateFrame)
      scrollStateFrame = null
    }
    if (previousScope?.instanceId && previousScope.tab) {
      saveScrollPosition(previousScope.instanceId, previousScope.tab, previousScope.groupId)
    }
    restoreHistory(getScrollKey(scope.instanceId, scope.tab, scope.groupId))
    restoreDraft()
    nextTick(() => {
      if (scope !== scrollScope.value) return
      const hadSavedScroll = restoreScrollPosition(scope.instanceId, scope.tab, scope.groupId)
      forceScrollOnNextMessageUpdate.value = !hadSavedScroll
    })
  },
  { immediate: true },
)

watch(inputText, () => {
  persistDraft()
})

function onComposerAttachmentsChanged() {
  composerRevision.value += 1
}

function onBubbleDrop(e) {
  fileDragDepth = 0
  dragOver.value = false
  e.preventDefault()
  if (!props.readOnly && hasDraggedFiles(e)) {
    e.stopPropagation()
    composerEl.value?.addFiles(e.dataTransfer?.files || [], undefined, "drop")
    return
  }
  if (!props.groupId || !Array.from(e.dataTransfer?.types || []).includes("application/x-kt-tab")) return
  e.stopPropagation()
  tabDrag.onBubbleDrop(e, props.groupId)
}

function onAttachmentError(error) {
  if (error.code === "too-large") {
    ElMessage.error(t("chat.attachmentTooLarge", { name: error.name, size: formatBytes(error.size), limit: formatBytes(error.limit) }))
  } else if (error.code === "not-image") {
    ElMessage.error(t("chat.attachmentNotImage", { name: error.name }))
  } else {
    ElMessage.error(error.error?.message || String(error.error || error.code))
  }
}

function transformAttachment(file, kind, source) {
  if (source !== "paste" || (file.name && file.name !== "image.png" && file.name !== "blob")) return file
  return renameClipboardBlob(file, kind || ((file.type || "").startsWith("image/") ? "image" : "file"))
}

function renameClipboardBlob(file, kind) {
  const ts = new Date().toISOString().replace(/[:.]/g, "-").replace(/T/, "_").replace(/Z$/, "")
  const ext = (file.type.split("/")[1] || (kind === "image" ? "png" : "bin")).split("+")[0] // image/svg+xml 閳?svg
  const stem = kind === "image" ? `pasted-image-${ts}` : `pasted-file-${ts}`
  try {
    return new File([file], `${stem}.${ext}`, {
      type: file.type,
      lastModified: file.lastModified,
    })
  } catch {
    return file
  }
}

async function send() {
  if (submitInFlight.value || props.readOnly || (!inputText.value.trim() && attachments.value.length === 0)) return
  submitInFlight.value = true
  try {
    const sendTab = viewActiveTab.value
    if (!sendTab) {
      ElMessage.error("Select a chat before sending")
      return
    }
    const sendText = inputText.value
    const sendAttachments = [...attachments.value]
    const sendComposerRevision = composerRevision.value
    const sendInstanceGeneration = chat._instanceGeneration
    const sendInstanceId = chat._instanceId
    const sendGraphId = chat._instanceGraphId
    const sendPropInstanceId = props.instance?.id
    const sendPropGraphId = props.instance?.graph_id
    let ownedSlashTarget = chat._slashTargetByTab?.[sendTab]
    const contextChanged = () => chat._instanceGeneration !== sendInstanceGeneration || chat._instanceId !== sendInstanceId || chat._instanceGraphId !== sendGraphId || props.instance?.id !== sendPropInstanceId || props.instance?.graph_id !== sendPropGraphId || chat.activeTab !== sendTab || viewActiveTab.value !== sendTab || inputText.value !== sendText || attachments.value.length !== sendAttachments.length || attachments.value.some((attachment, index) => attachment !== sendAttachments[index])
    const clearOwnedSlashTarget = () => {
      if (chat._slashTargetByTab?.[sendTab] === ownedSlashTarget) {
        chat.markSlashTarget(sendTab, null)
      }
    }
    if (slashMenuOpen.value && slashMatches.value.length) {
      chooseSlashEntry(slashMatches.value[slashSelectedIndex.value] || slashMatches.value[0])
      return
    }
    if (props.groupId) onGroupFocus()
    let slashTarget = null
    try {
      slashTarget = await chat.prepareSlashSend(
        {
          key: sendTab,
          creature: sendTab,
          type: sendTab?.startsWith("ch:") ? "channel" : "creature",
        },
        sendText,
      )
    } catch (err) {
      console.warn("Slash inventory lookup failed; using command fallback:", err)
    }
    if (contextChanged()) {
      clearOwnedSlashTarget()
      return
    }
    chat.markSlashTarget(sendTab, slashTarget)
    ownedSlashTarget = chat._slashTargetByTab?.[sendTab]
    let parts
    try {
      parts = await buildMessageParts(sendText, sendAttachments)
    } catch (err) {
      clearOwnedSlashTarget()
      throw err
    }
    if (contextChanged()) {
      clearOwnedSlashTarget()
      return
    }
    const parsedCommand = _parseSlashCommand(parts)
    const inlineCommand = parsedCommand?.command === "goal" && (!slashTarget || (slashTarget.type === "command" && slashTarget.name.toLowerCase() === "goal"))
    if (!parsedCommand && slashTarget) clearOwnedSlashTarget()
    const resultContext = inlineCommand ? chat.registerCommandResultContext(sendTab) : null
    if (contextChanged()) {
      if (inlineCommand) chat.releaseCommandResultContext(sendTab, resultContext)
      clearOwnedSlashTarget()
      return
    }
    const commandTarget = {
      sessionId: sendGraphId || sendInstanceId,
      creatureId: sendTab || "root",
      tabKey: sendTab,
      commandText: sendText,
      inline: inlineCommand,
      resultContext,
    }
    const commandContextChanged = () => chat._instanceGeneration !== sendInstanceGeneration || chat._instanceId !== sendInstanceId || chat._instanceGraphId !== sendGraphId || props.instance?.id !== sendPropInstanceId || props.instance?.graph_id !== sendPropGraphId
    const canUseHttp = sendTab.startsWith("ch:") || inlineCommand
    const stillOwnsComposer = () => composerRevision.value === sendComposerRevision && chat._instanceGeneration === sendInstanceGeneration && chat._instanceId === sendInstanceId && chat._instanceGraphId === sendGraphId && props.instance?.id === sendPropInstanceId && props.instance?.graph_id === sendPropGraphId && chat.activeTab === sendTab && viewActiveTab.value === sendTab && inputText.value === sendText && attachments.value.length === sendAttachments.length && attachments.value.every((attachment, index) => attachment === sendAttachments[index])
    if (!canUseHttp && chat._ws?.readyState !== WebSocket.OPEN) {
      if (inlineCommand) chat.releaseCommandResultContext(sendTab, resultContext)
      clearOwnedSlashTarget()
      ElMessage.error("Chat is not connected")
      return
    }
    let outcomePromise
    try {
      outcomePromise = chat.send(parts)
      let outcome = null
      if (!canUseHttp) outcome = await outcomePromise
      if (stillOwnsComposer()) {
        inputText.value = ""
        attachments.value = []
        persistDraft()
        nextTick(() => composerEl.value?.resetHeight())
      }
      isNearBottom.value = true // force scroll after send
      leaveHistory()
      scrollScheduler.resume()
      scheduleScrollToBottom(true)
      if (canUseHttp) outcome = await outcomePromise
      if (outcome?.handled === "command") {
        if (commandContextChanged()) {
          chat.releaseCommandResultContext(commandTarget.tabKey, commandTarget.resultContext)
        } else {
          await surfaceCommandResult(outcome.result, commandTarget)
        }
      } else if (commandTarget.inline) {
        chat.releaseCommandResultContext(commandTarget.tabKey, commandTarget.resultContext)
      }
    } catch (err) {
      console.error("Command failed:", err)
      if (commandContextChanged()) {
        chat.releaseCommandResultContext(commandTarget.tabKey, commandTarget.resultContext)
        return
      }
      if (commandTarget.inline) {
        chat.addCommandResult(
          commandTarget.tabKey,
          commandTarget.commandText,
          {
            error: err?.response?.data?.detail || err?.message || String(err),
          },
          commandTarget.resultContext,
        )
        if (viewActiveTab.value === commandTarget.tabKey) scheduleScrollToBottom(true)
      } else {
        ElMessage.error(`Command failed: ${err?.message || err}`)
      }
    }
  } finally {
    submitInFlight.value = false
  }
}

async function triggerCompact() {
  if (props.readOnly) return
  if (props.groupId) onGroupFocus()
  try {
    const sid = chat._instanceGraphId || chat._instanceId
    const tab = viewActiveTab.value || "root"
    const response = await terrariumAPI.executeCreatureCommand(sid, tab, "compact")
    await surfaceCommandResult(response)
  } catch (err) {
    console.error("Compact failed:", err)
    ElMessage.error(`Compact failed: ${err?.message || err}`)
  }
}

async function surfaceCommandResult(response, target = null) {
  if (!response) return
  if (target?.inline) {
    chat.addCommandResult(target.tabKey, target.commandText, response, target.resultContext)
    if (viewActiveTab.value === target.tabKey) scheduleScrollToBottom(true)
    return
  }
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
  if (payload && payload.type === "confirm" && payload.message && payload.action) {
    try {
      await ElMessageBox.confirm(payload.message, response.output || payload.action, {
        type: "warning",
        confirmButtonText: t("common.confirm"),
        cancelButtonText: t("common.cancel"),
      })
    } catch {
      return
    }
    const sid = target?.sessionId || chat._instanceGraphId || chat._instanceId
    const tab = target?.creatureId || viewActiveTab.value || "root"
    const confirmed = await terrariumAPI.executeCreatureCommand(sid, tab, payload.action, payload.action_args || "")
    await surfaceCommandResult(confirmed, { sessionId: sid, creatureId: tab })
    return
  }
  if (response.output) {
    ElMessage({ message: response.output, type: "info" })
  }
}

async function triggerClear() {
  if (props.readOnly) return
  if (props.groupId) onGroupFocus()
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
    const tab = viewActiveTab.value || "root"
    const response = await terrariumAPI.executeCreatureCommand(sid, tab, "clear", "--force")
    await surfaceCommandResult(response)
  } catch (err) {
    console.error("Clear failed:", err)
    ElMessage.error(`Clear failed: ${err?.message || err}`)
  }
}

async function stopTask(jobId, jobName) {
  try {
    const tab = viewActiveTab.value
    const sid = chat._instanceGraphId || chat._instanceId
    await terrariumAPI.stopCreatureTask(sid, tab || "root", jobId)
    const job = chat.runningJobs[jobId]
    if (job) job.cancelling = true
  } catch (err) {
    console.error("Failed to stop task:", err)
  }
}

function onGlobalKeydown(e) {
  if (props.readOnly) return
  if (e.defaultPrevented) return
  if (props.groupId && !isFocusedGroup.value) return
  if (e.key === "Escape" && viewProcessing.value) {
    chat.interrupt(viewActiveTab.value)
  }
}
onMounted(() => window.addEventListener("keydown", onGlobalKeydown))
onUnmounted(() => {
  window.removeEventListener("keydown", onGlobalKeydown)
  scrollScheduler.dispose()
  historyExpander.dispose()
  if (scrollStateFrame !== null) cancelAnimationFrame(scrollStateFrame)
})
</script>

<style scoped src="./chat-panel.css"></style>
