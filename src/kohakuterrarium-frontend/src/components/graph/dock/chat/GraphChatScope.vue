<template>
  <div class="h-full overflow-hidden">
    <ChatPanelContainer v-if="instance" :instance="instance" />
    <div v-else class="h-full flex items-center justify-center text-xs text-warm-500">{{ loading ? t("graph.chat.loading") : t("graph.chat.unavailable") }}</div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch } from "vue"

import ChatPanelContainer from "@/components/chat/ChatPanelContainer.vue"
import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { acquireScope, provideScope, releaseScope } from "@/composables/useScope"
import { useChatStore } from "@/stores/chat"
import { useInstancesStore } from "@/stores/instances"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  sessionId: { type: String, required: true },
  innerTab: { type: String, default: null },
})

const { t } = useI18n()
const instances = useInstancesStore()

// The dock is a scope owner like an attach tab: it holds a reference so the
// scoped chat store and its socket live exactly as long as the dock shows it.
provideScope(props.sessionId)
acquireScope(props.sessionId)
const chat = useChatStore(props.sessionId)

const instance = ref(null)
const loading = ref(true)
let poll = null

async function load() {
  try {
    const loaded = await instances.fetchOne(props.sessionId)
    if (!loaded) {
      instance.value = null
      return
    }
    instance.value = loaded
    const sameInstance = chat._instanceId === loaded.id || chat._instanceId === loaded.graph_id
    if (chat._instanceId && !sameInstance) chat.resetForRouteSwitch()
    chat.initForInstance(loaded, { initialTab: sameInstance ? null : props.innerTab })
  } finally {
    loading.value = false
  }
}

watch(
  () => props.innerTab,
  (tab) => {
    if (tab && instance.value && chat.activeTab !== tab) chat.openTab(tab)
  },
)

onMounted(async () => {
  await load()
  if (props.innerTab && chat.activeTab !== props.innerTab) chat.openTab(props.innerTab)
  poll = createVisibilityInterval(() => load().catch(() => {}), 5000)
  poll.start()
})

onUnmounted(() => {
  poll?.stop()
  releaseScope(props.sessionId)
})
</script>
