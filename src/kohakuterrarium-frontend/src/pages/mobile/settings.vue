<template>
  <MobileShell :title="t('common.settings')">
    <SettingsPage />
  </MobileShell>
</template>

<script setup>
import MobileShell from "@/components/mobile/MobileShell.vue"
import SettingsPage from "@/pages/settings.vue"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
</script>
