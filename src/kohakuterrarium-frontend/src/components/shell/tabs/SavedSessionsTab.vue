<template>
  <HistoryPage />
</template>

<script setup>
import HistoryPage from "@/components/shell/history/HistoryPage.vue"

defineProps({ tab: { type: Object, required: true } })
</script>
