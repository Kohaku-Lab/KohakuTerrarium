<template>
  <div>
    <div class="flex items-center justify-between px-3 py-1">
      <span class="kt-text-caption uppercase tracking-wider text-warm-500 font-medium"> {{ t("shell.rail.conversations") }} </span>
      <span class="kt-text-caption text-warm-400">{{ conversations.rows.length }}</span>
    </div>
    <div class="flex flex-col gap-0.5">
      <RailItem v-for="conversation in conversations.rows" :key="conversation.id" :instance="conversation" />
      <div v-if="conversations.rows.length === 0" class="px-3 py-2 text-[11px] text-warm-400 italic">{{ t("shell.rail.conversationsEmpty") }}</div>
    </div>
  </div>
</template>

<script setup>
import RailItem from "@/components/shell/RailItem.vue"
import { useConversationsStore } from "@/stores/conversations"
import { useI18n } from "@/utils/i18n"

const conversations = useConversationsStore()
const { t } = useI18n()
</script>
