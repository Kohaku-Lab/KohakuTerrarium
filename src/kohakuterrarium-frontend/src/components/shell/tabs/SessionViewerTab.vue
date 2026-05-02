<template>
  <SessionViewerPage :session-name-prop="tab.name" />
</template>

<script setup>
// Embed: pages/sessions/[name].vue accepts a sessionNameProp that wins
// over route.params.name. Same component, different invocation path.
import SessionViewerPage from "@/pages/sessions/[name].vue"

defineProps({ tab: { type: Object, required: true } })
</script>
