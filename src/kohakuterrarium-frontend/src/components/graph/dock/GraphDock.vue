<template>
  <aside class="h-full flex flex-col min-h-0 bg-warm-50 dark:bg-warm-950">
    <div class="flex items-center gap-1 h-9 px-2 border-b border-warm-200 dark:border-warm-700 shrink-0">
      <button v-for="tab in tabs" :key="tab.id" class="px-2.5 h-7 rounded text-xs flex items-center gap-1.5" :class="activeTab === tab.id ? 'bg-iolite/12 text-iolite font-medium' : 'text-warm-500 hover:text-warm-800 dark:hover:text-warm-200'" :disabled="tab.disabled" @click="activeTab = tab.id"><span :class="tab.icon" />{{ tab.label }}</button>
      <span class="flex-1" />
      <button class="i-carbon-close text-warm-400 hover:text-warm-700 dark:hover:text-warm-200" :title="t('graph.dock.close')" @click="$emit('close')" />
    </div>

    <div v-show="activeTab === 'details'" class="flex-1 min-h-0 overflow-y-auto p-3">
      <ConfirmCard v-if="pending" class="mb-3" :request="pending" @cancel="pending = null" @done="pending = null" />
      <CreatureDetails v-if="sel?.kind === 'creature'" :view="view" :actions="actions" :creature="sel.item" @open-chat="activeTab = 'chat'" @confirm="pending = $event" />
      <ChannelDetails v-else-if="sel?.kind === 'channel'" :view="view" :actions="actions" :channel="sel.item" @open-chat="activeTab = 'chat'" @confirm="pending = $event" />
      <EdgeDetails v-else-if="sel?.kind === 'edge'" :view="view" :actions="actions" :edge="sel.item" />
      <GroupDetails v-else-if="sel?.kind === 'group'" :view="view" :actions="actions" :group="sel.item" @confirm="pending = $event" />
      <SessionSummary v-else :view="view" :actions="actions" @quick-add="$emit('quick-add', $event)" @confirm="pending = $event" @new-session="$emit('new-session')" />
    </div>

    <div v-if="chatMounted" v-show="activeTab === 'chat'" class="flex-1 min-h-0">
      <div v-if="view.isSample" class="h-full flex items-center justify-center text-xs text-warm-500 px-6 text-center">{{ t("graph.chat.sample") }}</div>
      <div v-else-if="!chatSessionId" class="h-full flex items-center justify-center text-xs text-warm-500 px-6 text-center">{{ t("graph.chat.pick") }}</div>
      <GraphChatScope v-else :key="chatSessionId" :session-id="chatSessionId" :inner-tab="chatInnerTab" />
    </div>
  </aside>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import ConfirmCard from "@/components/graph/dock/details/ConfirmCard.vue"
import CreatureDetails from "@/components/graph/dock/details/CreatureDetails.vue"
import ChannelDetails from "@/components/graph/dock/details/ChannelDetails.vue"
import EdgeDetails from "@/components/graph/dock/details/EdgeDetails.vue"
import GroupDetails from "@/components/graph/dock/details/GroupDetails.vue"
import SessionSummary from "@/components/graph/dock/details/SessionSummary.vue"
import GraphChatScope from "@/components/graph/dock/chat/GraphChatScope.vue"
import { deleteRequestForSelection } from "@/components/graph/dock/details/deleteRequests"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  view: { type: Object, required: true },
  actions: { type: Object, required: true },
})
defineEmits(["close", "quick-add", "new-session"])

const { t } = useI18n()

const activeTab = ref("details")
const pending = ref(null)
const chatMounted = ref(false)

const sel = computed(() => props.view.selected)

const tabs = computed(() => [
  { id: "details", label: t("graph.dock.details"), icon: "i-carbon-information" },
  { id: "chat", label: t("graph.dock.chat"), icon: "i-carbon-chat" },
])

const chatTarget = computed(() => {
  const s = sel.value
  if (s?.kind === "creature") {
    const session = props.view.model.sessions.find((x) => x.id === s.item.sessionId)
    const rootTab = s.item.root && session?.creatureIds.length > 1
    return { sessionId: s.item.sessionId, tab: rootTab ? "root" : s.item.name }
  }
  if (s?.kind === "channel") return { sessionId: s.item.sessionId, tab: `ch:${s.item.name}` }
  return null
})

const lastChatTarget = ref(null)
watch(
  chatTarget,
  (target) => {
    if (target) lastChatTarget.value = target
    pending.value = null
  },
  { immediate: true },
)

const chatSessionId = computed(() => lastChatTarget.value?.sessionId || props.view.effectiveSessionId || null)
const chatInnerTab = computed(() => (lastChatTarget.value?.sessionId === chatSessionId.value ? lastChatTarget.value?.tab : null))

watch(activeTab, (tab) => {
  if (tab === "chat") chatMounted.value = true
})

function openChat() {
  activeTab.value = "chat"
}

function requestDelete() {
  activeTab.value = "details"
  pending.value = deleteRequestForSelection(props.view, props.actions, t)
}

function showDetails() {
  activeTab.value = "details"
}

defineExpose({ openChat, requestDelete, showDetails })
</script>
