<template>
  <RegistryPage />
</template>

<script setup>
// Thin embed: RegistryPage is a self-contained component.
import RegistryPage from "@/components/registry/RegistryPage.vue"

defineProps({ tab: { type: Object, required: true } })
</script>
