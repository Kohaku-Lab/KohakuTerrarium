<template>
  <div class="h-full flex flex-col min-h-0" data-test="v2-side-peek">
    <div class="shrink-0 flex items-center gap-2 px-3 h-9 border-b kt-v2-line text-[12px]">
      <span v-if="isChannel" class="text-aquamarine font-semibold">#</span>
      <span v-else class="i-carbon-bot text-warm-400" />
      <span class="font-medium text-warm-700 dark:text-warm-200 truncate">{{ label }}</span>
      <span class="flex-1" />
      <button class="h-6 px-2 rounded text-[11px] text-iolite hover:bg-iolite/10" @click="openInChat">{{ t("side.peek.open") }}</button>
    </div>
    <div v-if="!isOpen" class="flex-1 flex flex-col items-center justify-center gap-2 text-xs text-warm-400">
      <span>{{ t("side.peek.notOpen") }}</span>
      <button class="text-iolite hover:underline" @click="openInChat">{{ t("side.peek.open") }}</button>
    </div>
    <ChatTranscriptSection v-else class="flex-1 min-h-0 kt-v2-peek" :messages="windowMessages" :message-offset="windowStart" :total-count="messages.length" :previous-message="windowStart > 0 ? messages[windowStart - 1] : null" :empty-title="t('side.peek.empty')" :can-load-earlier="windowStart > 0" :earlier-count="windowStart" :earlier-label="t('side.peek.earlier', { n: windowStart })" :render-message="renderMessage" @load-earlier="showEarlier" @viewport-ready="onViewport" />
  </div>
</template>

<script setup>
import { computed, h, nextTick, provide, watch } from "vue"

import ChatMessage from "@/components/chat/ChatMessage.vue"
import { useChatRenderWindow } from "@/components/chat/chatRenderWindow"
import { ChatTranscriptSection } from "@kohakuterrarium/chat-ui"
import { tabLabel } from "@/components/session-v2/model/creatureKeys"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/** A read-only view of another conversation (`payload.key`, a creature or `ch:<name>`) beside the chat, tail-windowed. */
const props = defineProps({ payload: { type: Object, default: () => ({}) } })

const ctx = useSessionV2()
const t = useV2T()
provide("chatStore", ctx.chat)

const key = computed(() => props.payload.key || "")
const isChannel = computed(() => key.value.startsWith("ch:"))
const label = computed(() => (isChannel.value ? key.value.slice(3) : tabLabel(key.value, ctx.chat._rootSourceName)))
const isOpen = computed(() => (ctx.chat.tabs || []).includes(key.value))
const messages = computed(() => ctx.chat.messagesByTab?.[key.value] || [])
const { windowMessages, windowStart, enterHistoryAt, expandHistory, leaveHistory } = useChatRenderWindow(messages, () => `v2-peek:${key.value}`)

let viewport = null

function renderMessage(message, context) {
  return h(ChatMessage, {
    message,
    prevMessage: context.previousMessage,
    isFirst: context.isFirst,
    messageIdx: context.absoluteIndex,
    isLastAssistant: context.isLastAssistant,
    tabId: key.value,
  })
}

function nearBottom() {
  return !viewport || viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 80
}

function toBottom() {
  if (viewport) viewport.scrollTop = viewport.scrollHeight
}

function onViewport(el) {
  viewport = el
  nextTick(toBottom)
}

function showEarlier() {
  enterHistoryAt(windowStart.value)
  expandHistory()
}

function openInChat() {
  ctx.chat.openTab(key.value)
  ctx.closeSide()
}

watch(
  key,
  (k) => {
    leaveHistory()
    if (k && isOpen.value) ctx.chat.ensureVisibleHistory?.(k)
    nextTick(toBottom)
  },
  { immediate: true },
)
// Follow new messages only while the reader is at the bottom.
watch(
  () => messages.value.length,
  () => {
    if (nearBottom()) nextTick(toBottom)
  },
)
</script>
