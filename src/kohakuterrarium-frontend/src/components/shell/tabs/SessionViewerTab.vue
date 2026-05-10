<template>
  <SessionViewerPage :session-name-prop="tab.name" />
</template>

<script setup>
// Embed: SessionViewerPage accepts a sessionNameProp that wins over
// route.params.name. Same component, different invocation path.
import SessionViewerPage from "@/components/sessions/pages/SessionViewerPage.vue"

defineProps({ tab: { type: Object, required: true } })
</script>
