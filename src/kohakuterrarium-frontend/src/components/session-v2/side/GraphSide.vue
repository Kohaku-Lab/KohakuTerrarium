<template>
  <div class="h-full flex flex-col min-h-0" data-test="v2-side-graph">
    <GraphSurface v-if="sessionId" class="flex-1 min-h-0" :store-key="`v2:${sessionId}`" :session-id="sessionId" lock-session :embedded-chat="false" @open-chat="openChat" />
    <div v-else class="flex-1 flex items-center justify-center text-xs text-warm-400">{{ t("side.graph.none") }}</div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import GraphSurface from "@/components/graph/GraphSurface.vue"
import { tabKeyFor } from "@/components/session-v2/model/creatureKeys"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/** The session's live graph beside the chat, locked to this session; picking a creature or channel opens its conversation. */
defineProps({ payload: { type: Object, default: () => ({}) } })

const ctx = useSessionV2()
const t = useV2T()
const sessionId = computed(() => ctx.sessionId.value || null)

function openChat(selected) {
  if (!selected) return
  if (selected.kind === "channel") ctx.chat.openTab(`ch:${selected.item.name}`)
  else if (selected.kind === "creature") ctx.chat.openTab(tabKeyFor(selected.item.name, ctx.chat._rootSourceName))
}
</script>
