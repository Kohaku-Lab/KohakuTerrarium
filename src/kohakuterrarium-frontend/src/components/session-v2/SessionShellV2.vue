<template>
  <div class="kt-v2 kt-v2-canvas h-full flex flex-col overflow-hidden" data-test="session-v2">
    <SessionBar :compact="isCompact" @stop="$emit('stop')" />
    <div class="flex-1 min-h-0 relative overflow-hidden">
      <KeepAlive :include="kept">
        <component :is="current" :key="ctx.tab.value" class="absolute inset-0" />
      </KeepAlive>
    </div>
    <TabSwitcher v-if="isCompact" bottom />
  </div>
</template>

<script setup>
import { computed, provide, toRef } from "vue"

import SessionBar from "@/components/session-v2/bar/SessionBar.vue"
import TabSwitcher from "@/components/session-v2/bar/TabSwitcher.vue"
import ChatTab from "@/components/session-v2/chat/ChatTab.vue"
import { TABS } from "@/components/session-v2/model/registry"
import { createSessionV2, provideSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useDensity } from "@/composables/useDensity"
import { useChatStore } from "@/stores/chat"

/**
 * The v2 session tab: the session bar over one whole-page tab (Chat,
 * Status, Workspace, Debug, Settings); on phones the tabs move to a
 * bottom bar. Provides the session context and the scoped chat store
 * the reused chat components inject.
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
  }),
)
const current = computed(() => (ctx.tab.value === "chat" ? ChatTab : TABS[ctx.tab.value]))
const kept = computed(() => (ctx.tab.value === "workspace" ? ALWAYS_KEPT : ["ChatTab", ...ALWAYS_KEPT]))
</script>

<style src="./surfaces.css"></style>
