<template>
  <SettingsPage :key="hostRevision" :section="tab.section || ''" :section-request="tab.sectionRequest || 0" @section-applied="tabs.acknowledgeSettingsSection(tab.id, $event)" />
</template>

<script setup>
import { ref, watch } from "vue"
import SettingsPage from "@/components/settings/SettingsPage.vue"
import { useHostsStore } from "@/stores/hosts"
import { invalidateProviderModels } from "@/utils/providerModels"
import { useTabsStore } from "@/stores/tabs"

defineProps({ tab: { type: Object, required: true } })
const tabs = useTabsStore()
const hosts = useHostsStore()
const hostRevision = ref(0)
// Restart the inventory, editors and dialogs together when their request destination changes.
watch(
  [() => hosts.activeHostId, () => hosts.activeBaseURL, () => hosts.activeUserToken],
  () => {
    hostRevision.value++
    invalidateProviderModels()
  },
  { flush: "sync" },
)
</script>
