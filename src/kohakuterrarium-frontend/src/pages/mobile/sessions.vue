<template>
  <MobileShell :title="t('common.sessions')">
    <SessionsPage />
  </MobileShell>
</template>

<script setup>
import MobileShell from "@/components/mobile/MobileShell.vue"
import SessionsPage from "@/pages/sessions/index.vue"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
</script>
