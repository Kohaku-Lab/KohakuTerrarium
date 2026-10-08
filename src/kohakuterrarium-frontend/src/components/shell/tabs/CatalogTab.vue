<template>
  <CatalogPanel :initial-tab="tab.kind === 'extensions' ? 'modules' : 'browse'" />
</template>

<script setup>
// The Library tab: marketplace, installed packages and the modules they contribute.
// An `extensions` tab (from an older layout) opens it on the modules list.
import CatalogPanel from "@/components/panels/catalog/CatalogTab.vue"

defineProps({ tab: { type: Object, required: true } })
</script>
