<template>
  <div class="flex-1 overflow-hidden">
    <!--
      ``:key`` includes the tab id and its revision counter; bumping
      the revision via ``tabs.refreshTab(id)`` forces Vue to tear
      down + remount the active component, re-running setup hooks
      (re-fetch data) without closing the tab.
    -->
    <component :is="ActiveComp" v-if="ActiveComp && tabs.activeTab" :key="contentKey" :tab="tabs.activeTab" />
    <PlaceholderTab v-else-if="tabs.activeTab" :tab="tabs.activeTab" />
    <PlaceholderTab v-else :tab="null" />
  </div>
</template>

<script setup>
import { computed } from "vue"

import PlaceholderTab from "@/components/shell/tabs/PlaceholderTab.vue"
import { useTabsStore } from "@/stores/tabs"
import { tabKindRegistry } from "@/stores/tabKindRegistry"

const tabs = useTabsStore()

const ActiveComp = computed(() => {
  const t = tabs.activeTab
  if (!t) return null
  return tabKindRegistry.get(t.kind)?.component ?? null
})

const contentKey = computed(() => {
  const t = tabs.activeTab
  if (!t) return "no-tab"
  return `${t.id}@${tabs.revisions[t.id] ?? 0}`
})
</script>
