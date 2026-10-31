<template>
  <SectionShell :title="t('settings.section.software')" :hint="t('settings.desc.software')" :error="update.error.value" bare>
    <div class="flex flex-col gap-6" data-test="software-section">
      <VersionCard :update="update" :diagnostics="diagnostics" />
      <UpdateSettingsGroup :update="update" />
      <AdvancedUpdateGroup :update="update" />
      <DetailsGroup :data="diagnostics" :loading="loading" :error="diagnosticsError" @reload="reload" />
    </div>

    <el-dialog :model-value="update.progress.open" :title="update.progress.phase || t('software.progress.working')" width="26rem" :close-on-click-modal="false" :show-close="false" append-to-body>
      <el-progress :percentage="Math.max(0, Math.min(100, update.progress.percent || 0))" :status="update.progress.status === 'ok' ? 'success' : undefined" />
      <p class="mt-3 min-h-4 font-mono text-[11px] text-warm-500 break-all">{{ update.progress.message }}</p>
      <p v-if="update.progress.status === 'ok' && update.progress.restartRequired" class="mt-3 text-sm text-iolite dark:text-iolite-light">{{ t("software.restart") }}</p>
      <template #footer>
        <KButton @click="update.closeProgress()">{{ update.progress.status ? t("common.close") : t("common.cancel") }}</KButton>
      </template>
    </el-dialog>
  </SectionShell>
</template>

<script setup>
import { ref } from "vue"

import SectionShell from "@/components/session-v2/settings/SectionShell.vue"
import AdvancedUpdateGroup from "@/components/settings/software/AdvancedUpdateGroup.vue"
import DetailsGroup from "@/components/settings/software/DetailsGroup.vue"
import UpdateSettingsGroup from "@/components/settings/software/UpdateSettingsGroup.vue"
import VersionCard from "@/components/settings/software/VersionCard.vue"
import KButton from "@/components/common/ActionButton.vue"
import { useAppUpdate } from "@/composables/useAppUpdate"
import { configAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const update = useAppUpdate()
const diagnostics = ref(null)
const loading = ref(false)
const diagnosticsError = ref("")

async function reload() {
  loading.value = true
  diagnosticsError.value = ""
  try {
    diagnostics.value = await configAPI.getDiagnostics()
  } catch (err) {
    diagnosticsError.value = err?.response?.data?.detail || err?.message || t("software.loadFailed")
  } finally {
    loading.value = false
  }
}

reload()
</script>
