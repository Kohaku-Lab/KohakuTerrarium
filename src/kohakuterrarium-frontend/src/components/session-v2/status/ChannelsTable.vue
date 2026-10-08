<template>
  <StatusSection id="channels" :title="t('status.channels')" icon="i-carbon-flow-stream" :count="rows.length" :empty="!rows.length" :empty-label="t('status.none.channels')">
    <table class="w-full text-xs">
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

const ctx = useSessionV2()
const t = useV2T()
const chat = ctx.chat

const rows = computed(() => channelRows(ctx.instance.value))

function openChat(name) {
  chat.openTab(`ch:${name}`)
  ctx.setTab("chat")
}
</script>
