<template>
  <MobileShell :title="t('common.registry')">
    <RegistryPage />
  </MobileShell>
</template>

<script setup>
import MobileShell from "@/components/mobile/MobileShell.vue"
import RegistryPage from "@/pages/registry.vue"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
</script>
