<template>
  <SessionsListPage :on-view="onView" :on-resume="onResume" />
</template>

<script setup>
// Embed: SessionsListPage accepts onView/onResume callbacks that win
// over its route-based defaults.
import SessionsListPage from "@/components/sessions/pages/SessionsListPage.vue"

import { useTabsStore } from "@/stores/tabs"

defineProps({ tab: { type: Object, required: true } })

const tabs = useTabsStore()

function onView(session) {
  tabs.openTab({
    kind: "session-viewer",
    id: `session:${session.name}`,
    name: session.name,
  })
}

function onResume({ session, result }) {
  // Open an attach tab on the resumed instance.
  if (result?.instance_id) {
    tabs.openSurface(result.instance_id, "chat", {
      config_name: session.name,
      type: "creature",
    })
  }
}
</script>
