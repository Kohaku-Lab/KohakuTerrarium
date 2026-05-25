<template>
  <CatalogPanel />
</template>

<script setup>
// The top-level Catalog tab — Browse marketplace + Installed packages
// with the unified install flow (marketplace spec / git URL / local
// path / editable).  Replaces the legacy RegistryPage which only
// surfaced the bundled static index.
import CatalogPanel from "@/components/panels/catalog/CatalogTab.vue"

defineProps({ tab: { type: Object, required: true } })
</script>
