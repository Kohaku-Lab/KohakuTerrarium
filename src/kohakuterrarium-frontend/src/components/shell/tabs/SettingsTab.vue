<template>
  <SettingsPage :section="tab.section || ''" :section-request="tab.sectionRequest || 0" @section-applied="tabs.acknowledgeSettingsSection(tab.id, $event)" />
</template>

<script setup>
// Thin embed: SettingsPage is a self-contained component.
import SettingsPage from "@/components/settings/SettingsPage.vue"
import { useTabsStore } from "@/stores/tabs"

defineProps({ tab: { type: Object, required: true } })
const tabs = useTabsStore()
</script>
