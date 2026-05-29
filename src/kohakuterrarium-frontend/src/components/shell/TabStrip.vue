<template>
  <div class="relative h-8 flex items-center bg-warm-100 dark:bg-warm-900 border-b border-warm-200 dark:border-warm-700 overflow-hidden">
    <div class="flex items-center overflow-x-auto h-8 flex-1" @dragover.prevent="onStripDragOver" @drop.prevent="onStripDrop">
      <TabItem v-for="t in stripTabs" :key="t.id" :tab="t" :active="t.id === activeId" :group-id="groupId" @activate="tabs.activateTab(t.id)" @close="tabs.closeTab(t.id)" @drop="onDrop(t.id, $event)" />
    </div>
    <button class="w-8 h-8 flex items-center justify-center text-warm-400 hover:text-warm-700 hover:bg-warm-200/50 dark:hover:bg-warm-800/50 shrink-0" :title="'New tab menu'" @click="openMenu">
      <span class="i-carbon-add text-sm" />
    </button>
    <NewTabMenu v-if="menuOpen" :group-id="groupId" @close="menuOpen = false" />
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import TabItem from "@/components/shell/TabItem.vue"
import NewTabMenu from "@/components/shell/NewTabMenu.vue"
import { useTabsStore } from "@/stores/tabs"
import { useSplitDrag } from "@/composables/useSplitDrag"

// Optional ``groupId``: when set, the strip shows only that group's
// tabs and reorders within it. When omitted, it operates on the global
// flat view (legacy / single-group callers).
const props = defineProps({
  groupId: { type: String, default: null },
})

const tabs = useTabsStore()
const menuOpen = ref(false)
const drag = useSplitDrag()

const stripTabs = computed(() => (props.groupId ? tabs.tabsInGroup(props.groupId) : tabs.tabs))
const activeId = computed(() => (props.groupId ? (tabs.tabGroups[props.groupId]?.activeId ?? null) : tabs.activeId))

function openMenu() {
  // New tabs land in this group — focus it before the menu opens.
  if (props.groupId) tabs.setFocusedGroup(props.groupId)
  menuOpen.value = !menuOpen.value
}

function onStripDragOver(ev) {
  if (props.groupId) drag.onTabStripDragOver(ev, props.groupId)
}

function onStripDrop(ev) {
  // Drop on empty strip area → append into this group.
  if (props.groupId) drag.onTabStripDrop(ev, props.groupId, -1)
}

function onDrop(targetId, ev) {
  // Cross-group tab drag (our typed payload) → move before the target.
  if (props.groupId && drag.isOurDrag(ev)) {
    const dstIndex = stripTabs.value.findIndex((t) => t.id === targetId)
    drag.onTabStripDrop(ev, props.groupId, dstIndex < 0 ? -1 : dstIndex)
    return
  }
  // Same-strip reorder via the legacy text/plain payload.
  const draggedId = ev?.dataTransfer?.getData("text/plain")
  if (!draggedId || draggedId === targetId) return
  const ids = stripTabs.value.map((t) => t.id)
  const fromIdx = ids.indexOf(draggedId)
  const toIdx = ids.indexOf(targetId)
  if (fromIdx < 0 || toIdx < 0) return
  ids.splice(fromIdx, 1)
  ids.splice(toIdx, 0, draggedId)
  tabs.reorderTabs(ids, props.groupId)
}
</script>
