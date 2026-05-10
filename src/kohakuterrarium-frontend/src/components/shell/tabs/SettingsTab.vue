<template>
  <SettingsPage />
</template>

<script setup>
// Thin embed: SettingsPage is a self-contained component.
import SettingsPage from "@/components/settings/SettingsPage.vue"

defineProps({ tab: { type: Object, required: true } })
</script>
