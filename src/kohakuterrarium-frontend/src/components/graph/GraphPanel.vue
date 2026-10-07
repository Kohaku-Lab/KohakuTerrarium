<template>
  <GraphSurface v-if="sessionId" :store-key="`panel:${sessionId}`" :session-id="sessionId" lock-session :embedded-chat="false" @open-chat="focusChat" />
  <div v-else class="h-full flex items-center justify-center text-xs text-warm-500">{{ t("graph.chat.unavailable") }}</div>
</template>

<script setup>
import { computed } from "vue"

import GraphSurface from "@/components/graph/GraphSurface.vue"
import { useChatStore } from "@/stores/chat"
import { useI18n } from "@/utils/i18n"

const props = defineProps({ instance: { type: Object, default: null } })

const { t } = useI18n()
const sessionId = computed(() => props.instance?.graph_id || props.instance?.id || null)

function focusChat(selected) {
  if (!sessionId.value || !selected) return
  const chat = useChatStore(props.instance?.id || sessionId.value)
  if (selected.kind === "channel") chat.openTab(`ch:${selected.item.name}`)
  else if (selected.kind === "creature") chat.openTab(selected.item.root && props.instance?.has_root ? "root" : selected.item.name)
}
</script>
