<template>
  <header class="kt-v2-chrome kt-v2-line shrink-0 h-12 flex items-center gap-1 pl-3 pr-1 border-b" data-test="phone-header">
    <span class="w-2 h-2 rounded-full shrink-0" :class="running ? 'bg-aquamarine' : 'bg-warm-400'" />
    <button v-if="onChat && current" type="button" class="flex-1 min-w-0 h-11 flex flex-col justify-center items-start text-left px-1.5 rounded-lg active:bg-warm-200/60 dark:active:bg-warm-800" :aria-label="t('phone.switchConversation')" data-test="phone-conversation-title" @click="ctx.openSheet('conversations')">
      <span class="text-[11px] leading-tight text-warm-500 truncate max-w-full">{{ sessionName }}</span>
      <span class="flex items-center gap-1 max-w-full text-[15px] leading-tight font-semibold text-warm-800 dark:text-warm-100">
        <span v-if="current.kind === 'channel'" class="text-aquamarine">#</span>
        <span class="truncate">{{ current.name }}</span>
        <span v-if="current.privileged" class="i-carbon-security text-iolite/80 shrink-0 text-sm" />
        <span v-if="otherUnread" class="px-1.5 rounded-full bg-amber text-white text-[10px] leading-4 font-bold shrink-0">{{ otherUnread }}</span>
        <span class="i-carbon-chevron-down text-warm-400 shrink-0 text-sm" />
      </span>
    </button>
    <div v-else class="flex-1 min-w-0 flex flex-col justify-center px-1.5">
      <span class="text-[11px] leading-tight text-warm-500 truncate">{{ sessionName }}</span>
      <span class="text-[15px] leading-tight font-semibold text-warm-800 dark:text-warm-100 truncate">{{ t(`tab.${ctx.tab.value}`) }}</span>
    </div>
    <button type="button" class="w-11 h-11 shrink-0 flex items-center justify-center rounded-full text-warm-600 dark:text-warm-300 active:bg-warm-200/60 dark:active:bg-warm-800" :aria-label="t('phone.menu')" data-test="phone-menu-open" @click="ctx.openSheet('menu')"><span class="i-carbon-overflow-menu-horizontal text-xl" /></button>
  </header>
</template>

<script setup>
import { computed } from "vue"

import { conversationRows } from "@/components/session-v2/model/phone/phoneModel"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/**
 * The phone session header: on the Chat tab the open conversation is the
 * title and opens the conversations sheet (with the unread count of the
 * others); on other tabs the tab's name. The ⋯ button opens the session menu.
 */
const ctx = useSessionV2()
const t = useV2T()

const running = computed(() => ctx.instance.value?.status === "running")
const sessionName = computed(() => {
  const inst = ctx.instance.value
  return inst?.config_name || inst?.creatures?.[0]?.name || ctx.instanceId.value
})
const onChat = computed(() => ctx.tab.value === "chat")
const rows = computed(() => conversationRows(ctx.instance.value, ctx.chat))
const current = computed(() => rows.value.find((r) => r.active) || rows.value[0] || null)
const otherUnread = computed(() => rows.value.reduce((n, r) => n + r.unread, 0))
</script>
