<template>
  <MobileShell :title="t('common.startNew')">
    <NewPage />
  </MobileShell>
</template>

<script setup>
import MobileShell from "@/components/mobile/MobileShell.vue"
import NewPage from "@/pages/new.vue"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
</script>
