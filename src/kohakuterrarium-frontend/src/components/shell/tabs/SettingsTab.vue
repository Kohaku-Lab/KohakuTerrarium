<template>
  <SettingsPage />
</template>

<script setup>
// Thin embed: settings.vue is a self-contained page (no route params).
import SettingsPage from "@/pages/settings.vue"

defineProps({ tab: { type: Object, required: true } })
</script>
