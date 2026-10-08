<template>
  <LabPage />
</template>

<script setup>
import LabPage from "@/components/lab/LabPage.vue"

/** The fixed home tab: the lab. Studio is its own app, not a tab. */
defineProps({ tab: { type: Object, required: true } })
</script>
