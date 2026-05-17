<template>
  <div class="settings-pane max-w-2xl text-[13px]">
    <div v-if="loading" class="py-8 text-center text-secondary">{{ t("common.loading") }}</div>
    <div v-else-if="error" class="card p-4 text-coral text-sm">{{ error }}</div>
    <template v-else-if="data">
      <div class="text-center mb-4">
        <h2 class="text-2xl font-semibold text-warm-800 dark:text-warm-200">KohakuTerrarium</h2>
        <p class="text-warm-500 dark:text-warm-400 text-sm">
          {{ t("about.version") }} <span class="font-mono">{{ data.version }}</span>
          <span class="ml-2 text-[11px]">{{ data.install_kind === "wrapper" ? t("about.installWrapper") : t("about.installUser") }}</span>
        </p>
      </div>

      <section class="card p-4 mb-3">
        <h3 class="font-medium text-warm-700 dark:text-warm-300 mb-2">{{ t("about.runtime") }}</h3>
        <dl class="grid grid-cols-[140px_1fr] gap-y-1 gap-x-3 text-[12px]">
          <dt class="text-warm-400">Python</dt>
          <dd class="font-mono">{{ data.python.version }} ({{ data.python.implementation }}, {{ data.python.bits }})</dd>
          <dt class="text-warm-400">{{ t("about.platform") }}</dt>
          <dd class="font-mono">{{ data.platform.system }} {{ data.platform.release }} ({{ data.platform.machine }})</dd>
          <dt class="text-warm-400">{{ t("about.installKind") }}</dt>
          <dd class="font-mono">{{ data.install_kind }}</dd>
        </dl>
      </section>

      <section class="card p-4 mb-3">
        <h3 class="font-medium text-warm-700 dark:text-warm-300 mb-2">{{ t("about.paths") }}</h3>
        <dl class="grid grid-cols-[140px_1fr] gap-y-1 gap-x-3 text-[12px]">
          <dt class="text-warm-400">{{ t("about.pathHome") }}</dt>
          <dd class="font-mono break-all">{{ data.paths.home }}</dd>
          <dt class="text-warm-400">{{ t("about.pathSessions") }}</dt>
          <dd class="font-mono break-all">{{ data.paths.sessions }}</dd>
          <dt class="text-warm-400">{{ t("about.pathPackages") }}</dt>
          <dd class="font-mono break-all">{{ data.paths.packages }}</dd>
          <dt class="text-warm-400">{{ t("about.pathLogs") }}</dt>
          <dd class="font-mono break-all">{{ data.paths.logs }}</dd>
          <dt class="text-warm-400">{{ t("about.pathVenv") }}</dt>
          <dd class="font-mono break-all">{{ data.paths.venv }}</dd>
        </dl>
      </section>

      <section class="card p-4 mb-3">
        <h3 class="font-medium text-warm-700 dark:text-warm-300 mb-2">{{ t("about.daemon") }}</h3>
        <dl class="grid grid-cols-[140px_1fr] gap-y-1 gap-x-3 text-[12px]">
          <dt class="text-warm-400">PID</dt>
          <dd class="font-mono">{{ data.daemon.pid }}</dd>
          <dt class="text-warm-400">{{ t("about.uptime") }}</dt>
          <dd class="font-mono">{{ formatUptime(data.daemon.uptime_seconds) }}</dd>
          <dt class="text-warm-400">{{ t("about.mode") }}</dt>
          <dd class="font-mono">{{ data.daemon.mode }}</dd>
          <dt v-if="data.daemon.lab_bind" class="text-warm-400">{{ t("about.labBind") }}</dt>
          <dd v-if="data.daemon.lab_bind" class="font-mono">{{ data.daemon.lab_bind }}</dd>
        </dl>
      </section>

      <div class="flex gap-2 mt-4">
        <el-button size="small" plain @click="copyDiagnostics">
          <span class="i-carbon-copy mr-1" />
          {{ t("about.copy") }}
        </el-button>
        <el-button size="small" plain @click="logsOpen = true">
          <span class="i-carbon-document mr-1" />
          {{ t("about.viewLogs") }}
        </el-button>
        <el-button size="small" plain @click="reload">
          <span class="i-carbon-renew mr-1" />
          {{ t("common.refresh") }}
        </el-button>
      </div>
    </template>
    <ServerLogsModal v-model="logsOpen" />
  </div>
</template>

<script setup>
import { ref } from "vue"
import { ElMessage } from "element-plus"

import ServerLogsModal from "@/components/diagnostics/ServerLogsModal.vue"
import { configAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const logsOpen = ref(false)

const { t } = useI18n()

const data = ref(null)
const loading = ref(false)
const error = ref("")

async function reload() {
  loading.value = true
  error.value = ""
  try {
    data.value = await configAPI.getDiagnostics()
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "load failed"
  } finally {
    loading.value = false
  }
}

function formatUptime(s) {
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${sec}s`
  return `${sec}s`
}

async function copyDiagnostics() {
  if (!data.value) return
  const txt = JSON.stringify(data.value, null, 2)
  try {
    await navigator.clipboard.writeText(txt)
    ElMessage.success(t("about.copied"))
  } catch {
    ElMessage.warning(t("about.copyFailed"))
  }
}

reload()
</script>
