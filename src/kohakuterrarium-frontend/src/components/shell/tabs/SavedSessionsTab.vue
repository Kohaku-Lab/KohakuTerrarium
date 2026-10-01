<template>
  <SessionsListPage :on-view="onView" :on-resume="onResume" />
</template>

<script setup>
import { savedSessionLabel } from "@/utils/sessionLabels"
// Embed: SessionsListPage accepts onView/onResume callbacks that win
// over its route-based defaults.
import SessionsListPage from "@/components/sessions/pages/SessionsListPage.vue"

import { useTabsStore } from "@/stores/tabs"
import { resumedTabMeta } from "@/utils/resumedTab"

defineProps({ tab: { type: Object, required: true } })

const tabs = useTabsStore()

function onView(session) {
  tabs.openTab({
    kind: "session-viewer",
    id: `session:${session.name}`,
    name: session.name,
    config_name: savedSessionLabel(session),
  })
}

function onResume({ session, result }) {
  // Open an attach tab on the resumed instance.
  if (result?.instance_id) {
    tabs.openSurface(result.instance_id, "chat", resumedTabMeta(result, session.name))
  }
}
</script>
