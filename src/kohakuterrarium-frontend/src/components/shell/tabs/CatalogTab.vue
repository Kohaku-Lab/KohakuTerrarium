<template>
  <RegistryPage />
</template>

<script setup>
// Thin embed: registry.vue is a self-contained page (no route params).
import RegistryPage from "@/pages/registry.vue"

defineProps({ tab: { type: Object, required: true } })
</script>
