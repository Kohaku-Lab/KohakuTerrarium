<template>
  <div class="kt-v2-chatcol h-full min-h-0 flex flex-col relative" :class="{ 'ring-2 ring-inset ring-iolite/30': dragOver }" :style="reserveRight ? { paddingRight: '23.5rem' } : null" @dragenter="onDragEnter" @dragleave="onDragLeave" @dragover="onDragOver" @drop="onDrop">
    <ConversationStrip v-if="strip" :narrow="narrow" />
    <div v-if="empty" class="flex-1 min-h-0 flex flex-col items-center justify-end pb-6 px-4">
      <ChatEmptyHero :tab-key="tabKey" />
    </div>
    <ChatTranscriptSection v-else class="kt-conversation-host kt-v2-transcript flex-1 min-h-0" :class="narrow ? 'kt-v2-transcript--narrow' : ''" :messages="viewport.windowMessages.value" :message-offset="viewport.windowStart.value" :total-count="viewport.messages.value.length" :previous-message="viewport.windowStart.value > 0 ? viewport.messages.value[viewport.windowStart.value - 1] : null" :empty-title="it('chat.noMessagesYet')" :empty-subtitle="it('chat.getStarted')" :processing="showProcessing" :processing-label="processingLabel" :reconnecting="chat.wsStatus === 'reconnecting'" :reconnect-label="it('chat.disconnected')" :partial="!!chat.tokenUsage[tabKey]?.partial" :partial-label="t('transcript.partial')" :has-newer="!!pageState?.hasNewer" :newer-label="t('transcript.newer')" :reset-required="!!pageState?.resetRequired" :reset-label="t('transcript.reset')" :history-blocked="viewport.historyBlocked.value" :history-blocked-label="it('chat.loadEarlierGenerating')" :can-load-earlier="!viewport.historyBlocked.value && (viewport.windowStart.value > 0 || viewport.hasOlderHistory.value)" :earlier-count="viewport.windowStart.value" :earlier-label="viewport.windowStart.value ? it('chat.showEarlier', { count: viewport.windowStart.value }) : it('sessionViewer.trace.turn.loadMore')" :render-message="renderMessage" @load-earlier="viewport.loadEarlier" @reload="reloadHistory" @scroll="viewport.handlers.onScroll" @viewport-ready="viewport.handlers.onViewportReady" @wheel="viewport.handlers.onWheel" @keydown="viewport.handlers.onKeydown" @touchstart="viewport.handlers.onTouchStart" @touchmove="viewport.handlers.onTouchMove" />
    <div class="shrink-0 px-4" :class="empty ? '' : 'pb-4 pt-2'">
      <div class="mx-auto w-full" :class="narrow ? '' : 'kt-v2-read'">
        <div v-if="historyError" class="mb-1.5 flex items-center gap-2 text-xs text-coral" role="alert">
          <span class="flex-1 min-w-0 truncate">{{ historyError }}</span>
          <button class="i-carbon-close shrink-0 hover:text-coral-shadow" :title="it('common.close')" @click="historyError = ''" />
        </div>
        <ChatComposerBar :composer="composer" :chat="chat" :tab-key="tabKey" @show-pending="viewport.scrollToMessage" />
      </div>
    </div>
    <div v-if="empty" class="flex-1 min-h-0" />
    <div v-if="dragOver" class="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
      <span class="kt-v2-float kt-v2-edge rounded-lg border px-3 py-1.5 text-xs text-iolite dark:text-iolite-light shadow-sm"><span class="i-carbon-attachment mr-1 align-[-2px]" />{{ t("chat.dropHint") }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, h, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from "vue"

import ChatMessage from "@/components/chat/ChatMessage.vue"
import ChatComposerBar from "@/components/session-v2/chat/ChatComposerBar.vue"
import ChatEmptyHero from "@/components/session-v2/chat/ChatEmptyHero.vue"
import ConversationStrip from "@/components/session-v2/chat/ConversationStrip.vue"
import { useComposerSend } from "@/components/session-v2/chat/useComposerSend"
import { useTranscriptViewport } from "@/components/session-v2/chat/useTranscriptViewport"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { ChatTranscriptSection } from "@kohakuterrarium/chat-ui"
import { useI18n } from "@/utils/i18n"

/**
 * The session's one chat column: the transcript of the active
 * conversation in a centered reading column and the composer under it;
 * an empty conversation shows a greeting with the composer centered.
 * `narrow` fits it to the Workspace tab's side column; `strip` shows the
 * conversation chips above it where no rail lists them.
 */
defineProps({
  narrow: { type: Boolean, default: false },
  strip: { type: Boolean, default: true },
  // Keeps the right lane free for an open floating widget.
  reserveRight: { type: Boolean, default: false },
})

const ctx = useSessionV2()
const chat = ctx.chat
const { t: it } = useI18n()
const t = useV2T()
const tabKey = computed(() => chat.activeTab || null)
const instanceId = computed(() => ctx.instance.value?.id || chat._instanceId || null)

watch(
  () => [tabKey.value, chat._instanceGeneration, chat.wsStatus, chat._historyLoaded],
  () => void chat.ensureVisibleHistory(tabKey.value),
  { immediate: true },
)

const viewport = useTranscriptViewport({ chat, tabKey, instanceId, positions: ctx.column.scrollPositions })
const composer = useComposerSend({ chat, tabKey, instance: ctx.instance, attachments: ctx.column.attachments, onSent: () => viewport.followAfterSend() })

const pageState = computed(() => (tabKey.value ? chat.historyPageByTab?.[tabKey.value] : null))
const empty = computed(() => chat._historyLoaded && viewport.messages.value.length === 0 && !viewport.processing.value && !pageState.value?.hasOlder)
watch(empty, (isEmpty) => isEmpty && viewport.detach())

const historyError = ref("")
const errorText = (err) => err?.response?.data?.detail || err?.message || String(err)
watch(tabKey, () => (historyError.value = ""))

async function reloadHistory() {
  historyError.value = ""
  try {
    await viewport.reloadHistory()
  } catch (err) {
    historyError.value = t("transcript.historyFailed", { error: errorText(err) })
  }
}

const runningJobs = computed(() => chat.runningJobCountForTab(tabKey.value))
const recovery = computed(() => chat.modelRecoveryForTab?.(tabKey.value))
const streaming = computed(() => chat.processing && chat.viewingRunningBranch)
const showProcessing = computed(() => !!recovery.value || runningJobs.value > 0 || streaming.value)
const processingLabel = computed(() => {
  const n = runningJobs.value
  if (recovery.value) {
    const key = recovery.value === "waiting" ? "chat.processingRetry" : "chat.processingRecovery"
    return it(n ? `${key}Bg` : key, { n })
  }
  if (streaming.value && n) return it("chat.processingStreamingBg", { n })
  if (streaming.value) return it("chat.processingStreaming")
  if (n) return it("chat.processingWaitingBg", { n })
  return it("chat.processing")
})

const detailPending = ref(null)
async function loadHistoryDetail(key) {
  if (detailPending.value !== null) return
  detailPending.value = key
  historyError.value = ""
  try {
    await chat.loadHistoryRecord(tabKey.value, key)
  } catch (err) {
    historyError.value = t("transcript.historyFailed", { error: errorText(err) })
  } finally {
    detailPending.value = null
  }
}

function renderMessage(message, context) {
  const row = h(ChatMessage, {
    message,
    prevMessage: context.previousMessage,
    isFirst: context.isFirst,
    messageIdx: context.absoluteIndex,
    isLastAssistant: context.isLastAssistant,
    tabId: tabKey.value,
  })
  const details = message._historyDetails || []
  if (!details.length) return row
  return h("div", { class: "flex flex-col" }, [
    ...details.map((key) =>
      h(
        "button",
        {
          key: `history-detail:${key}`,
          "data-history-detail": key,
          disabled: detailPending.value !== null,
          class: "self-start text-xs text-iolite hover:underline",
          onClick: () => loadHistoryDetail(key),
        },
        it("sessionViewer.detail.title"),
      ),
    ),
    row,
  ])
}

const dragOver = ref(false)
let dragDepth = 0
const hasFiles = (ev) => Array.from(ev.dataTransfer?.types || []).includes("Files")
function onDragEnter(ev) {
  if (!hasFiles(ev)) return
  ev.preventDefault()
  dragDepth += 1
  dragOver.value = true
}
function onDragLeave(ev) {
  if (!hasFiles(ev)) return
  dragDepth = Math.max(0, dragDepth - 1)
  if (!dragDepth) dragOver.value = false
}
function onDragOver(ev) {
  if (hasFiles(ev)) ev.preventDefault()
}
function onDrop(ev) {
  dragDepth = 0
  dragOver.value = false
  if (!hasFiles(ev)) return
  ev.preventDefault()
  composer.composerEl.value?.addFiles?.(ev.dataTransfer?.files || [], undefined, "drop")
}

function onGlobalKeydown(e) {
  if (!e.defaultPrevented && e.key === "Escape" && ctx.focused.value && viewport.processing.value) chat.interrupt(tabKey.value)
}
// The shown column's compact / clear serve the phone session menu.
const columnActions = { compact: () => composer.compact(), clear: () => composer.clear() }
const listen = () => {
  window.addEventListener("keydown", onGlobalKeydown)
  ctx.column.actions.value = columnActions
}
const unlisten = () => {
  window.removeEventListener("keydown", onGlobalKeydown)
  if (ctx.column.actions.value === columnActions) ctx.column.actions.value = null
}
onMounted(listen)
onActivated(listen)
onDeactivated(unlisten)
onUnmounted(unlisten)
</script>

<style scoped>
/* Messages and composer share one centered reading column: 48rem, widening to 66rem on a wide column. */
.kt-v2-chatcol {
  container-type: inline-size;
}
.kt-v2-transcript :deep(.chat-messages-viewport) {
  container-type: size;
}
.kt-v2-read {
  max-width: clamp(48rem, 66cqi, 66rem);
}
.kt-v2-transcript :deep(.kt-conversation-list) {
  width: 100%;
  max-width: clamp(48rem, 66cqi, 66rem);
  margin-inline: auto;
  padding-inline: 1rem;
}
.kt-v2-transcript--narrow :deep(.kt-conversation-list) {
  max-width: none;
}
</style>
