<template>
  <div class="h-full min-h-0 flex overflow-hidden">
    <ConversationRail v-if="showRail" />
    <div class="flex-1 min-w-0 min-h-0 relative">
      <ChatColumn :reserve-right="reserveForWidget" :strip="false" />
      <button v-if="activeChannel && !isCompact && !membersOpen" class="kt-v2-float kt-v2-edge absolute top-3 right-4 z-20 h-8 px-2.5 rounded-lg border shadow-sm flex items-center gap-1.5 text-xs text-warm-600 dark:text-warm-300 hover:text-iolite" :title="t('members.show')" data-test="channel-members-show" @click="setMembersOpen(true)"><span class="i-carbon-user-multiple" />{{ t("members.title") }}</button>
      <div v-if="!isCompact" class="absolute right-4 bottom-24 z-30 flex flex-col items-end gap-2 pointer-events-none">
        <div v-if="ctx.widget.value" class="pointer-events-auto"><WidgetFrame :id="ctx.widget.value" :key="ctx.widget.value" /></div>
        <div class="pointer-events-auto"><Dock /></div>
      </div>
    </div>
    <template v-if="!isCompact">
      <ChannelMembers v-if="activeChannel && membersOpen" :channel="activeChannel" @hide="setMembersOpen(false)" />
      <SideView v-if="ctx.side.value" :side="ctx.side.value" />
      <AddDialog v-if="ctx.addKind.value" :key="ctx.addKind.value" :kind="ctx.addKind.value" />
    </template>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue"

import AddDialog from "@/components/session-v2/add/AddDialog.vue"
import ChannelMembers from "@/components/session-v2/chat/ChannelMembers.vue"
import ChatColumn from "@/components/session-v2/chat/ChatColumn.vue"
import ConversationRail from "@/components/session-v2/chat/ConversationRail.vue"
import Dock from "@/components/session-v2/dock/Dock.vue"
import WidgetFrame from "@/components/session-v2/dock/WidgetFrame.vue"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import SideView from "@/components/session-v2/side/SideView.vue"
import { useDensity } from "@/composables/useDensity"
import { useCanvasStore } from "@/stores/canvas"
import { readLocalPref, writeLocalPref } from "@/utils/uiPrefs"

/**
 * The Chat tab: the conversation rail (agents and channels) on the left,
 * the chat column, the open channel's member list (shown by default,
 * hideable), the dock with its floating widget at the column's
 * bottom-right, the side view when one is open, and the "Add to session"
 * dialog. Phones get only the chat column: the header's conversations
 * sheet replaces the rail and the phone overlays host widgets, side views
 * and the dialog. The first artifact of a session opens the canvas side
 * view once.
 */
const MEMBERS_KEY = "kt.v2.members.open"
const ctx = useSessionV2()
const t = useV2T()
const canvas = useCanvasStore()
const { isExpansive, isCompact } = useDensity()
const activeChannel = computed(() => {
  const tab = ctx.chat.activeTab || ""
  return tab.startsWith("ch:") ? tab.slice(3) : ""
})
const membersOpen = ref(readLocalPref(MEMBERS_KEY) !== "0")
function setMembersOpen(value) {
  membersOpen.value = value
  writeLocalPref(MEMBERS_KEY, value ? "1" : "0")
}
const showRail = computed(() => !isCompact.value)
// On wide screens an open widget takes its own lane, so it never covers the composer; beside a side view the column is too narrow for a lane.
const reserveForWidget = computed(() => !!ctx.widget.value && isExpansive.value && !ctx.side.value)
const hasArtifacts = computed(() => canvas.artifacts.length > 0)

// Artifacts found in the loaded history count as seen (baseline taken a tick after
// history lands, once the detector scanned it); the flag lives in the context so a remount keeps it.
watch(
  () => [ctx.chat._historyLoaded, hasArtifacts.value],
  async ([loaded, has]) => {
    if (!loaded) return
    if (ctx.column.canvasAutoOpened === null) {
      await nextTick()
      if (ctx.column.canvasAutoOpened === null) ctx.column.canvasAutoOpened = hasArtifacts.value
      return
    }
    if (!has || ctx.column.canvasAutoOpened) return
    ctx.column.canvasAutoOpened = true
    if (!ctx.side.value && !isCompact.value) ctx.openSide("canvas", { index: 0 })
  },
  { immediate: true },
)
</script>
