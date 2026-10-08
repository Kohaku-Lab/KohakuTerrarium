<template>
  <div class="h-full min-h-0 overflow-y-auto py-1" data-test="v2-widget-channels">
    <div v-if="!rows.length" class="px-3 py-6 text-center text-xs text-warm-400">{{ t("widget.channels.empty") }}</div>
    <div v-for="r in rows" :key="r.key" class="group flex items-start gap-2.5 px-3 py-2 hover:bg-warm-100 dark:hover:bg-warm-800/60" :class="ctx.chat.activeTab === r.key ? 'bg-aquamarine/5' : ''">
      <span class="text-aquamarine font-semibold leading-5 shrink-0">#</span>
      <button class="min-w-0 flex-1 text-left" @click="open(r)">
        <div class="flex items-center gap-1.5 text-[13px] text-warm-800 dark:text-warm-100 min-w-0">
          <span class="truncate font-medium">{{ r.name }}</span>
          <span v-if="r.unread" class="px-1.5 rounded-full bg-amber text-white text-[9px] leading-4 font-bold shrink-0">{{ r.unread }}</span>
        </div>
        <div class="text-[11px] text-warm-400 truncate">{{ preview(r) || t("widget.channels.members", { n: r.members }) }}</div>
        <div v-if="mode === 'side'" class="mt-1 flex flex-wrap gap-1">
          <span v-for="name in r.senders" :key="`s:${name}`" class="px-1.5 rounded bg-aquamarine/15 text-[10px] text-aquamarine-shadow dark:text-aquamarine-light">↑ {{ name }}</span>
          <span v-for="name in r.listeners" :key="`l:${name}`" class="kt-v2-b px-1.5 rounded border border-aquamarine/40 text-[10px] text-warm-500">↓ {{ name }}</span>
        </div>
      </button>
      <button class="h-6 w-6 flex items-center justify-center rounded text-warm-400 hover:text-iolite hover:bg-iolite/10 shrink-0" :title="t('widget.channels.peek')" :data-test="`v2-channel-peek-${r.name}`" @click="peek(r)"><span class="i-carbon-side-panel-open" /></button>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { channelRows } from "@/components/session-v2/model/widgets/widgetData"

/** Designed channels of the session: unread, last message, members; open one in chat or peek at it beside chat. */
defineProps({ mode: { type: String, default: "widget" } })

const ctx = useSessionV2()
const t = useV2T()

const rows = computed(() => channelRows(ctx.instance.value, ctx.chat.unreadCounts || {}))

// The tail message of a loaded channel conversation (one array read per row).
function preview(r) {
  const list = ctx.chat.messagesByTab?.[r.key]
  const last = list?.[list.length - 1]
  if (!last) return ""
  const text = typeof last.content === "string" ? last.content : ""
  return text ? t("widget.channels.last", { sender: last.sender || "?", text: text.slice(0, 120) }) : ""
}

function open(r) {
  ctx.chat.openTab(r.key)
  ctx.setTab("chat")
  ctx.closeWidget()
}

function peek(r) {
  ctx.closeWidget()
  ctx.openSide("peek", { key: r.key })
}
</script>
