<template>
  <div class="flex-1 overflow-hidden">
    <!--
      ``:key`` includes the tab id and its revision counter; bumping
      the revision via ``tabs.refreshTab(id)`` forces Vue to tear
      down + remount the active component, re-running setup hooks
      (re-fetch data) without closing the tab.
    -->
    <UnderDensityPlaceholder v-if="underDensity" :required="underDensity.required" :panel-label="underDensity.label" />
    <component :is="ActiveComp" v-else-if="ActiveComp && tabs.activeTab" :key="contentKey" :tab="tabs.activeTab" />
    <PlaceholderTab v-else-if="tabs.activeTab" :tab="tabs.activeTab" />
    <PlaceholderTab v-else :tab="null" />
  </div>
</template>

<script setup>
import { computed } from "vue"

import UnderDensityPlaceholder from "@/components/shell/UnderDensityPlaceholder.vue"
import PlaceholderTab from "@/components/shell/tabs/PlaceholderTab.vue"
import { useDensity, meetsDensity } from "@/composables/useDensity"
import { useTabsStore } from "@/stores/tabs"
import { tabKindRegistry } from "@/stores/tabKindRegistry"

const tabs = useTabsStore()
const { density } = useDensity()

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

// `null` when the active tab is allowed at current density. Otherwise
// `{ required, label }` describing what to render in the placeholder.
const underDensity = computed(() => {
  const t = tabs.activeTab
  if (!t) return null
  const entry = tabKindRegistry.get(t.kind)
  if (!entry) return null
  const required = entry.minDensity ?? "compact"
  if (meetsDensity(density.value, required)) return null
  return { required, label: t.label || t.kind }
})
</script>
