<template>
  <div v-if="options.length > 1" class="kt-v2-line shrink-0 border-b" data-test="conversation-strip">
    <div class="flex items-center gap-1.5 px-3 py-2 overflow-x-auto scrollbar-none" :class="narrow ? '' : 'justify-center'">
      <template v-for="(o, i) in options" :key="o.key">
        <span v-if="i > 0 && o.kind !== options[i - 1].kind" class="kt-v2-edge mx-1 h-5 border-l shrink-0" />
        <button class="kt-v2-b h-7 px-3 rounded-full border flex items-center gap-1.5 text-xs shrink-0 transition-colors" :class="o.key === chat.activeTab ? 'bg-iolite/12 border-iolite/50 text-iolite dark:text-iolite-light font-medium' : 'kt-v2-panel kt-v2-line text-warm-600 dark:text-warm-300 hover:border-iolite/40 hover:text-warm-800 dark:hover:text-warm-100'" :title="o.privileged ? `${o.name} · ${t('privileged')}` : o.name" :data-test="`switch-${o.key}`" @click="chat.openTab(o.key)">
          <span v-if="o.kind === 'channel'" class="text-aquamarine">#</span>
          <span v-else-if="chat.processingByTab[o.key]" class="i-carbon-circle-dash animate-spin text-aquamarine" />
          <StatusDot v-else :status="o.status" />
          <span>{{ o.name }}</span>
          <span v-if="o.privileged" class="i-carbon-security text-iolite/80" />
          <span v-if="chat.unreadCounts[o.key] && o.key !== chat.activeTab" class="px-1.5 rounded-full bg-amber text-white text-[9px] leading-4 font-bold">{{ chat.unreadCounts[o.key] }}</span>
        </button>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import StatusDot from "@/components/common/StatusDot.vue"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { conversationOptions } from "@/components/session-v2/model/sessionModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/**
 * One-click conversation switching above the transcript: creatures
 * (privileged first) then designed channels, each a chip with busy and
 * unread marks. Hidden when the session has a single conversation.
 */
defineProps({ narrow: { type: Boolean, default: false } })

const ctx = useSessionV2()
const chat = ctx.chat
const t = useV2T()
const options = computed(() => conversationOptions(ctx.instance.value, chat._rootSourceName))
</script>
