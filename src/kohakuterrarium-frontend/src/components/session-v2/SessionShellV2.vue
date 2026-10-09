<template>
  <div class="kt-v2 kt-v2-canvas h-full flex flex-col overflow-hidden" data-test="session-v2">
    <PhoneHeader v-if="isCompact" />
    <SessionBar v-else @stop="$emit('stop')" />
    <div class="flex-1 min-h-0 relative overflow-hidden">
      <KeepAlive :include="kept">
        <component :is="current" :key="ctx.tab.value" class="absolute inset-0" />
      </KeepAlive>
    </div>
    <TabSwitcher v-if="isCompact" bottom />
    <PhoneOverlays v-if="isCompact" @stop="$emit('stop')" />
  </div>
</template>

<script setup>
import { computed, provide, toRef } from "vue"

import SessionBar from "@/components/session-v2/bar/SessionBar.vue"
import TabSwitcher from "@/components/session-v2/bar/TabSwitcher.vue"
import ChatTab from "@/components/session-v2/chat/ChatTab.vue"
import { TABS } from "@/components/session-v2/model/registry"
import { createSessionV2, provideSessionV2 } from "@/components/session-v2/model/sessionContext"
import PhoneHeader from "@/components/session-v2/phone/PhoneHeader.vue"
import PhoneOverlays from "@/components/session-v2/phone/PhoneOverlays.vue"
import { useDensity } from "@/composables/useDensity"
import { useChatStore } from "@/stores/chat"

/**
 * The v2 session tab: the session bar over one whole-page tab (Chat,
 * Graph, Status, Workspace, Debug, Settings). Phones get the phone header,
 * the tabs in a bottom bar and the phone overlays (sheets, full-screen
 * side views). Provides the session context and the scoped chat store the
 * reused chat components inject.
 */
const props = defineProps({
  instance: { type: Object, required: true },
  instanceId: { type: String, required: true },
  // Reloads the instance this shell shows (and the chat store's view of it).
  refresh: { type: Function, default: null },
  // Whether this shell takes keyboard shortcuts (false for a session shown in an unfocused split group).
  focused: { type: Boolean, default: true },
})
defineEmits(["stop"])

// Chat and Workspace both host a chat column, so Chat stays cached only while Workspace is not shown.
const ALWAYS_KEPT = ["StatusTab", "DebugTab", "SettingsTab"]
const { isCompact } = useDensity()
const chat = useChatStore()
provide("chatStore", chat)
const ctx = provideSessionV2(
  createSessionV2({
    instance: computed(() => props.instance),
    instanceId: toRef(props, "instanceId"),
    chat,
    focused: toRef(props, "focused"),
    refresh: () => props.refresh?.(),
    phone: isCompact,
  }),
)
const current = computed(() => (ctx.tab.value === "chat" ? ChatTab : TABS[ctx.tab.value]))
const kept = computed(() => (ctx.tab.value === "workspace" ? ALWAYS_KEPT : ["ChatTab", ...ALWAYS_KEPT]))
</script>
