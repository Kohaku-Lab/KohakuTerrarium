<template>
  <MessageRow :message="message" :prev-message="prevMessage" :is-first="isFirst" :message-idx="messageIdx" :is-last-assistant="isLastAssistant" :tab-id="tabId" />
</template>

<script setup>
import { h } from "vue"

import { MessageRow, provideMessageActions } from "@kohakuterrarium/chat-ui"
import SiteChip from "@/components/cluster/SiteChip.vue"
import { useInstancesStore } from "@/stores/instances"

defineProps({
  message: { type: Object, required: true },
  prevMessage: { type: Object, default: null },
  isFirst: { type: Boolean, default: false },
  messageIdx: { type: Number, default: null },
  isLastAssistant: { type: Boolean, default: false },
  tabId: { type: String, default: "" },
})

// Dashboard provider wrapper: installs the Dashboard-owned seams the shared
// production MessageRow consumes. The Studio cluster store + SiteChip stay here
// (never imported into the VS Code webview); the shared leaf only sees node ids.
const instances = useInstancesStore()
provideMessageActions({
  markdownOrigin: window.location.origin,
  renderSiteChip: (nodeId) => h(SiteChip, { nodeId }),
  resolveHomeNode: (message) => {
    if (message.role !== "channel") return ""
    const inst = instances.current
    if (!inst) return ""
    const c = (inst.creatures || []).find((c) => c.name === message.sender)
    return c?.home_node || inst.home_node || "_host"
  },
})
</script>
