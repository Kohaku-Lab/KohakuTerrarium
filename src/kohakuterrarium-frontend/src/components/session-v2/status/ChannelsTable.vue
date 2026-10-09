<template>
  <StatusSection id="channels" :title="t('status.channels')" icon="i-carbon-flow-stream" :count="rows.length" :empty="!rows.length" :empty-label="t('status.none.channels')">
    <div v-if="isCompact">
      <button v-for="row in rows" :key="row.name" type="button" class="kt-v2-line border-x-0 border-t-0 w-full min-h-14 flex items-center gap-3 px-4 py-2 border-b last:border-b-0 text-left active:bg-warm-200/60 dark:active:bg-warm-800" :data-test="`status-channel-${row.name}`" @click="openChat(row.name)">
        <span class="text-aquamarine font-semibold shrink-0">#</span>
        <span class="flex-1 min-w-0">
          <span class="block truncate text-sm font-medium text-warm-800 dark:text-warm-100">{{ row.name }}</span>
          <span class="block truncate text-xs text-warm-500">{{ row.senders.join(", ") || "—" }} → {{ row.listeners.join(", ") || "—" }}</span>
        </span>
        <span v-if="chat.unreadCounts[`ch:${row.name}`]" class="px-2 rounded-full bg-amber text-white text-[11px] leading-5 font-bold shrink-0">{{ chat.unreadCounts[`ch:${row.name}`] }}</span>
        <span class="i-carbon-chevron-right text-warm-400 shrink-0" />
      </button>
    </div>
    <table v-else class="w-full text-xs">
      <thead class="text-warm-400 text-left">
        <tr class="border-b kt-v2-line">
          <th class="font-medium px-4 py-2">{{ t("status.col.name") }}</th>
          <th class="font-medium px-2 py-2">{{ t("status.col.senders") }}</th>
          <th class="font-medium px-2 py-2">{{ t("status.col.listeners") }}</th>
          <th class="font-medium px-2 py-2 text-right">{{ t("status.unread") }}</th>
          <th class="px-4 py-2" />
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.name" class="border-b last:border-b-0 kt-v2-line hover:bg-warm-50 dark:hover:bg-warm-800/40 cursor-pointer" :data-test="`status-channel-${row.name}`" @click="openChat(row.name)">
          <td class="px-4 py-2 font-medium text-warm-800 dark:text-warm-100"><span class="text-aquamarine mr-1">#</span>{{ row.name }}</td>
          <td class="px-2 py-2 text-warm-600 dark:text-warm-300">{{ row.senders.join(", ") || "—" }}</td>
          <td class="px-2 py-2 text-warm-600 dark:text-warm-300">{{ row.listeners.join(", ") || "—" }}</td>
          <td class="px-2 py-2 text-right">
            <span v-if="chat.unreadCounts[`ch:${row.name}`]" class="px-1.5 rounded-full bg-amber text-white text-[10px] font-bold">{{ chat.unreadCounts[`ch:${row.name}`] }}</span>
          </td>
          <td class="px-4 py-2 text-right">
            <button class="px-2 py-0.5 rounded border kt-v2-line text-warm-600 dark:text-warm-300 hover:text-iolite hover:border-iolite/40" @click.stop="openChat(row.name)">{{ t("status.openChat") }}</button>
          </td>
        </tr>
      </tbody>
    </table>
  </StatusSection>
</template>

<script setup>
import { computed } from "vue"

import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { channelRows } from "@/components/session-v2/model/status/statusModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import StatusSection from "@/components/session-v2/status/StatusSection.vue"
import { useDensity } from "@/composables/useDensity"

/** The session's designed channels with senders, listeners and unread; a row opens the channel's chat. Phones get one row per channel. */
const ctx = useSessionV2()
const t = useV2T()
const { isCompact } = useDensity()
const chat = ctx.chat

const rows = computed(() => channelRows(ctx.instance.value))

function openChat(name) {
  chat.openTab(`ch:${name}`)
  ctx.setTab("chat")
}
</script>
