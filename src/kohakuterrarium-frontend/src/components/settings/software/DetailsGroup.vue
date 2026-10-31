<template>
  <div class="flex flex-col gap-6" data-test="software-details">
    <p v-if="loading && !data" class="text-sm text-warm-500">{{ t("common.loading") }}</p>
    <p v-else-if="error" class="text-sm text-coral" role="alert">{{ error }}</p>
    <template v-else-if="data">
      <SettingsGroup :title="t('about.runtime')">
        <SettingsRow v-for="row in runtimeRows" :key="row.label" :title="row.label" inline>
          <span class="font-mono text-xs text-warm-600 dark:text-warm-300 text-right break-all">{{ row.value }}</span>
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup :title="t('about.paths')">
        <SettingsRow v-for="row in pathRows" :key="row.label" :title="row.label">
          <template #detail>
            <div class="mt-0.5 font-mono text-[12px] text-warm-600 dark:text-warm-300 break-all" :title="row.value">{{ row.value }}</div>
          </template>
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup :title="t('about.daemon')">
        <SettingsRow v-for="row in processRows" :key="row.label" :title="row.label" inline>
          <span class="font-mono text-xs text-warm-600 dark:text-warm-300 text-right break-all">{{ row.value }}</span>
        </SettingsRow>
      </SettingsGroup>

      <div class="flex flex-wrap gap-2">
        <KButton size="sm" icon="i-carbon-copy" @click="copyDiagnostics">{{ t("about.copy") }}</KButton>
        <KButton size="sm" icon="i-carbon-document" @click="logsOpen = true">{{ t("about.viewLogs") }}</KButton>
        <KButton size="sm" variant="ghost" icon="i-carbon-renew" @click="$emit('reload')">{{ t("common.refresh") }}</KButton>
      </div>
    </template>
    <ServerLogsModal v-model="logsOpen" />
  </div>
</template>

<script setup>
import { computed, ref } from "vue"
import { ElMessage } from "element-plus"

import ServerLogsModal from "@/components/diagnostics/ServerLogsModal.vue"
import SettingsGroup from "@/components/settings/ui/SettingsGroup.vue"
import SettingsRow from "@/components/settings/ui/SettingsRow.vue"
import KButton from "@/components/common/ActionButton.vue"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  data: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: "" },
})
defineEmits(["reload"])

const { t } = useI18n()
const logsOpen = ref(false)

function formatUptime(seconds) {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m ${seconds % 60}s`
  return `${seconds}s`
}

const runtimeRows = computed(() => {
  const { python, platform, install_kind: kind } = props.data
  return [
    { label: "Python", value: `${python.version} (${python.implementation}, ${python.bits})` },
    { label: t("about.platform"), value: `${platform.system} ${platform.release} (${platform.machine})` },
    { label: t("about.installKind"), value: kind },
  ]
})

const pathRows = computed(() => {
  const { paths } = props.data
  return [
    { label: t("about.pathHome"), value: paths.home },
    { label: t("about.pathSessions"), value: paths.sessions },
    { label: t("about.pathPackages"), value: paths.packages },
    { label: t("about.pathLogs"), value: paths.logs },
    { label: t("about.pathVenv"), value: paths.venv },
  ]
})

const processRows = computed(() => {
  const { daemon } = props.data
  const rows = [
    { label: "PID", value: String(daemon.pid) },
    { label: t("about.uptime"), value: formatUptime(daemon.uptime_seconds) },
    { label: t("about.mode"), value: daemon.mode },
  ]
  if (daemon.lab_bind) rows.push({ label: t("about.labBind"), value: daemon.lab_bind })
  return rows
})

async function copyDiagnostics() {
  try {
    await navigator.clipboard.writeText(JSON.stringify(props.data, null, 2))
    ElMessage.success(t("about.copied"))
  } catch {
    ElMessage.warning(t("about.copyFailed"))
  }
}
</script>
