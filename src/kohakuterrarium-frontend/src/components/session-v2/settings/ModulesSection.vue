<template>
  <SectionShell :title="t('settings.section.modules')" :hint="t('settings.modules.sessionHint')">
    <template #aside><CreaturePicker v-model="target" /></template>
    <ModuleSettings v-if="target" :key="`${sessionId}:${target}`" :session-id="sessionId" :creature-id="target" />
  </SectionShell>
</template>

<script setup>
import { computed } from "vue"

import ModuleSettings from "@/components/settings/modules/ModuleSettings.vue"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import CreaturePicker from "@/components/session-v2/settings/CreaturePicker.vue"
import SectionShell from "@/components/session-v2/settings/SectionShell.vue"
import { useSectionTarget } from "@/components/session-v2/settings/useSectionTarget"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const session = useSessionV2()
const target = useSectionTarget(session)
const sessionId = computed(() => session.sessionId.value)
</script>
