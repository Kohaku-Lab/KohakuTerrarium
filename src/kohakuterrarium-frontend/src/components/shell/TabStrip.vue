<template>
  <div class="relative h-8 flex items-center bg-warm-100 dark:bg-warm-900 border-b border-warm-200 dark:border-warm-700 overflow-hidden">
    <div class="flex items-center overflow-x-auto h-8 flex-1" @dragover.prevent>
      <TabItem v-for="t in tabs.tabs" :key="t.id" :tab="t" :active="t.id === tabs.activeId" @activate="tabs.activateTab(t.id)" @close="tabs.closeTab(t.id)" @drop="onDrop(t.id, $event)" />
    </div>
    <button class="w-8 h-8 flex items-center justify-center text-warm-400 hover:text-warm-700 hover:bg-warm-200/50 dark:hover:bg-warm-800/50 shrink-0" :title="'New tab menu'" @click="menuOpen = !menuOpen">
      <span class="i-carbon-add text-sm" />
    </button>
    <NewTabMenu v-if="menuOpen" @close="menuOpen = false" />
  </div>
</template>

<script setup>
import { ref } from "vue"

import TabItem from "@/components/shell/TabItem.vue"
import NewTabMenu from "@/components/shell/NewTabMenu.vue"
import { useTabsStore } from "@/stores/tabs"

const tabs = useTabsStore()
const menuOpen = ref(false)

function onDrop(targetId, ev) {
  const draggedId = ev?.dataTransfer?.getData("text/plain")
  if (!draggedId || draggedId === targetId) return
  const ids = tabs.tabs.map((t) => t.id)
  const fromIdx = ids.indexOf(draggedId)
  const toIdx = ids.indexOf(targetId)
  if (fromIdx < 0 || toIdx < 0) return
  ids.splice(fromIdx, 1)
  ids.splice(toIdx, 0, draggedId)
  tabs.reorderTabs(ids)
}
</script>
