<template>
  <div v-if="showTerminal || showAutostart || showKeychain || showTray || showSummon || showUpdate || backend.status.value" id="desktop-app-settings" class="border-t border-warm-200 dark:border-warm-700 pt-3 flex flex-col gap-3" data-desktop-app-settings>
    <div class="font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.title") }}</div>

    <div v-if="showTray || showRestore" class="desktop-section-title" data-section="window">{{ t("settings.desktop.sectionWindow") }}</div>

    <div v-if="showTray" class="flex items-start justify-between gap-4" data-setting="close-to-tray">
      <div>
        <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.trayTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.desktop.trayHint") }}</div>
      </div>
      <el-switch :model-value="closeToTray" @change="setTray" />
    </div>

    <div v-if="showRestore" class="flex items-start justify-between gap-4" data-setting="restore-windows">
      <div>
        <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.restoreTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.desktop.restoreHint") }}</div>
      </div>
      <el-switch :model-value="shell.settings.value?.restorePopouts !== false" @change="(value) => shell.patch({ restorePopouts: value })" />
    </div>

    <div v-if="showKeepAwake" class="flex items-start justify-between gap-4" data-setting="keep-awake">
      <div>
        <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.keepAwakeTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.desktop.keepAwakeHint") }}</div>
      </div>
      <select class="text-xs rounded border border-warm-300 dark:border-warm-600 bg-transparent px-2 py-1" data-keep-awake @change="(event) => shell.patch({ keepAwake: event.target.value })">
        <option value="never" :selected="shell.settings.value?.keepAwake === 'never'">{{ t("settings.desktop.keepAwakeNever") }}</option>
        <option value="running" :selected="shell.settings.value?.keepAwake === 'running'">{{ t("settings.desktop.keepAwakeRunning") }}</option>
        <option value="running-on-ac" :selected="shell.settings.value?.keepAwake === 'running-on-ac'">{{ t("settings.desktop.keepAwakeAc") }}</option>
      </select>
    </div>

    <div v-if="showAutostart" class="desktop-section-title" data-section="startup">{{ t("settings.desktop.sectionStartup") }}</div>

    <div v-if="showAutostart" class="flex items-start justify-between gap-4" data-setting="autostart">
      <div>
        <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.autostartTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.desktop.autostartHint") }}</div>
      </div>
      <el-switch :model-value="autostart" @change="setLogin" />
    </div>

    <div v-if="showAutostart && showRestore" class="flex items-start justify-between gap-4" data-setting="start-hidden">
      <div>
        <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.startHiddenTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.desktop.startHiddenHint") }}</div>
      </div>
      <el-switch :model-value="shell.settings.value?.startHidden === true" :disabled="!autostart" @change="(value) => shell.patch({ startHidden: value })" />
    </div>

    <div v-if="showSummon" class="desktop-section-title" data-section="shortcuts">{{ t("settings.desktop.sectionShortcuts") }}</div>

    <div v-if="showSummon" class="flex items-start justify-between gap-4" data-setting="summon-shortcut">
      <div>
        <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.summonTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.desktop.summonHint") }}</div>
        <div v-if="summonError" class="text-[11px] text-coral mt-1" data-summon-error>{{ t("settings.desktop.summonUnavailable") }}</div>
      </div>
      <div class="flex items-center gap-2">
        <el-button size="small" data-summon-record @click="recordSummon">{{ summonRecording ? t("settings.desktop.summonRecording") : summonShortcut ? displayAccelerator(summonShortcut) : t("settings.desktop.summonSet") }}</el-button>
        <el-button v-if="summonShortcut && !summonRecording" size="small" data-summon-clear @click="clearSummon">{{ t("settings.desktop.summonClear") }}</el-button>
      </div>
    </div>

    <div v-if="showUpdate" class="desktop-section-title" data-section="updates">{{ t("settings.desktop.sectionUpdates") }}</div>

    <div v-if="showUpdate" class="flex items-start justify-between gap-4" data-setting="shell-update">
      <div>
        <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.updateTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.desktop.updateHint") }}</div>
        <div v-if="updateStatus === 'current'" class="text-[11px] text-warm-400 mt-1" data-update-status>{{ t("settings.desktop.updateCurrent") }}</div>
        <div v-else-if="updateStatus === 'available'" class="text-[11px] text-iolite mt-1" data-update-status>{{ t("settings.desktop.updateAvailable", { version: updateVersion }) }}</div>
        <div v-else-if="updateStatus === 'failed'" class="text-[11px] text-coral mt-1" data-update-status>{{ t("settings.desktop.updateFailed") }}</div>
      </div>
      <el-button v-if="updateStatus === 'available'" size="small" type="primary" data-update-install :loading="updateBusy" @click="installUpdate">{{ t("settings.desktop.updateInstall") }}</el-button>
      <el-button v-else size="small" data-update-check :loading="updateBusy" @click="checkUpdate">{{ t("settings.desktop.updateCheck") }}</el-button>
    </div>

    <div v-if="backend.status.value" class="desktop-section-title" data-section="backend">{{ t("settings.desktop.sectionBackend") }}</div>

    <div v-if="backend.status.value" class="flex items-start justify-between gap-4" data-setting="backend">
      <div>
        <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.backendTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-1" data-backend-summary>{{ backendSummary }}</div>
      </div>
      <div class="flex items-center gap-2">
        <el-button size="small" data-backend-logs @click="openLogs">{{ t("settings.desktop.backendLogs") }}</el-button>
        <el-button size="small" data-backend-restart :loading="backend.status.value.state === 'restarting'" @click="backend.restart">{{ t("settings.desktop.backendRestart") }}</el-button>
      </div>
    </div>

    <div v-if="backend.status.value" class="flex items-start justify-between gap-4" data-setting="runtime">
      <div>
        <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.runtimeTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-1" data-runtime-summary>{{ runtimeSummary }}</div>
        <div v-if="runtime?.bundled_newer" class="text-[11px] text-amber mt-1" data-runtime-newer>{{ t("settings.desktop.runtimeNewer", { version: runtime.bundled }) }}</div>
      </div>
      <el-button size="small" :type="repairArmed ? 'danger' : 'default'" data-runtime-repair @click="repair">{{ repairArmed ? t("settings.desktop.runtimeRepairConfirm") : t("settings.desktop.runtimeRepair") }}</el-button>
    </div>

    <div v-if="backend.status.value" class="flex items-start justify-between gap-4" data-setting="diagnostics">
      <div>
        <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.diagnosticsTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.desktop.diagnosticsHint") }}</div>
        <div v-if="diagnosticsResult?.ok" class="text-[11px] text-iolite mt-1" data-diagnostics-result>{{ t("settings.desktop.diagnosticsSaved", { path: diagnosticsResult.path }) }}</div>
        <div v-else-if="diagnosticsResult" class="text-[11px] text-coral mt-1" data-diagnostics-result>{{ t("settings.desktop.diagnosticsFailed") }}</div>
      </div>
      <el-button size="small" :loading="diagnosticsBusy" data-diagnostics-export @click="exportBundle">{{ t("settings.desktop.diagnosticsExport") }}</el-button>
    </div>

    <div v-if="explorerVerbs !== null" class="desktop-section-title" data-section="explorer">{{ t("settings.desktop.sectionExplorer") }}</div>

    <div v-if="explorerVerbs !== null" class="flex items-start justify-between gap-4" data-setting="explorer-verbs">
      <div>
        <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.explorerTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.desktop.explorerHint") }}</div>
      </div>
      <el-switch :model-value="explorerVerbs" @change="setExplorer" />
    </div>

    <div v-if="showTerminal || showKeychain" class="desktop-section-title" data-section="integration">{{ t("settings.desktop.sectionIntegration") }}</div>

    <div v-if="showTerminal" class="flex items-start justify-between gap-4" data-setting="terminal-command">
      <div>
        <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.terminalTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.desktop.terminalHint") }}</div>
        <div v-if="shims.installed.length" class="text-[11px] text-warm-400 mt-1" data-terminal-location>{{ t("settings.desktop.terminalLocation", { dir: shims.target_dir }) }}</div>
        <div v-if="justChanged && terminalEnabled" class="text-[11px] text-iolite mt-1" data-terminal-reopen>{{ t("settings.desktop.terminalReopen") }}</div>
        <div v-if="error" class="text-[11px] text-coral mt-1" data-terminal-error>{{ error }}</div>
      </div>
      <el-switch :model-value="terminalEnabled" :loading="busy" @change="setTerminal" />
    </div>

    <div v-if="showKeychain" class="flex items-start justify-between gap-4" data-setting="keychain">
      <div>
        <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.keychainTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.desktop.keychainHint", { backend: secrets.backend }) }}</div>
        <div v-if="keychainError" class="text-[11px] text-coral mt-1" data-keychain-error>{{ keychainError }}</div>
      </div>
      <el-switch :model-value="secrets.mode === 'keychain'" :loading="keychainBusy" @change="setKeychain" />
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue"

import { formatMemory, formatUptime, useBackendStatus } from "@/composables/useBackendStatus"
import { useShellSettings } from "@/composables/useShellSettings"
import { acceleratorFromEvent, displayAccelerator } from "@/utils/accelerator"
import { appUpdateAPI } from "@/utils/appUpdateApi"
import { appSecretsAPI } from "@/utils/appSecretsApi"
import { appShimsAPI } from "@/utils/appShimsApi"
import { checkShellUpdate, desktopCapabilities, getAutostart, getCloseToTray, getExplorerVerbs, getSummonShortcut, setExplorerVerbs, exportDiagnostics, hasDesktopHost, hostSupports, installShellUpdate, onHostReady, openLogs, repairRuntime, setAutostart, setCloseToTray, setSummonShortcut } from "@/utils/hostBridge"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const shims = ref({ supported: false, installed: [], commands: [], target_dir: "", on_path: false })
const available = ref(false)
const busy = ref(false)
const error = ref("")
const justChanged = ref(false)
const autostart = ref(false)
const hostReady = ref(hasDesktopHost())

const terminalEnabled = computed(() => shims.value.commands.length > 0 && shims.value.installed.length === shims.value.commands.length)
const showTerminal = computed(() => available.value && shims.value.supported && shims.value.local)
const showAutostart = computed(() => hostReady.value && hostSupports("get_autostart"))
const shell = useShellSettings()
const backend = useBackendStatus()
const runtime = ref(null)
const repairArmed = ref(false)
const diagnosticsBusy = ref(false)
const diagnosticsResult = ref(null)
let repairTimer = null
const runtimeSummary = computed(() => (runtime.value ? t("settings.desktop.runtimeSummary", { active: runtime.value.active?.version || "-", shipped: runtime.value.bundled || "-" }) : ""))

async function loadRuntime() {
  try {
    runtime.value = await appUpdateAPI.getRuntime()
  } catch {
    runtime.value = null
  }
}

function repair() {
  if (!repairArmed.value) {
    repairArmed.value = true
    repairTimer = setTimeout(() => (repairArmed.value = false), 5000)
    return
  }
  clearTimeout(repairTimer)
  repairArmed.value = false
  void repairRuntime()
}

async function exportBundle() {
  diagnosticsBusy.value = true
  try {
    const result = await exportDiagnostics()
    diagnosticsResult.value = result.cancelled ? null : result
  } finally {
    diagnosticsBusy.value = false
  }
}
const backendSummary = computed(() => {
  const status = backend.status.value
  if (!status) return ""
  const parts = [t(`settings.desktop.backendState.${status.state}`)]
  if (status.memoryBytes != null) parts.push(formatMemory(status.memoryBytes))
  if (status.uptimeSec != null) parts.push(t("settings.desktop.backendUptime", { time: formatUptime(status.uptimeSec) }))
  if (status.restarts > 0) parts.push(t("settings.desktop.backendRestarts", { count: status.restarts }))
  return parts.join(" · ")
})
const showTray = computed(() => hostReady.value && hostSupports("get_close_to_tray"))
const showRestore = computed(() => hostReady.value && shell.settings.value !== null)
const showKeepAwake = computed(() => showRestore.value && hostSupports("set_keep_awake"))
const explorerVerbs = ref(null)

async function setExplorer(enabled) {
  if (await setExplorerVerbs(enabled)) explorerVerbs.value = enabled
}
const secrets = ref({ local: false, mode: "file", available: false, backend: "" })
const showKeychain = computed(() => secrets.value.local && secrets.value.available)
const keychainBusy = ref(false)
const keychainError = ref("")
const closeToTray = ref(false)
const showSummon = computed(() => hostReady.value && hostSupports("get_summon_shortcut"))
const summonShortcut = ref(null)
const summonRecording = ref(false)
const summonError = ref(false)
const showUpdate = computed(() => hostReady.value && hostSupports("check_shell_update"))
const updateStatus = ref("idle")
const updateVersion = ref("")
const updateBusy = ref(false)

async function checkUpdate() {
  updateBusy.value = true
  try {
    const result = await checkShellUpdate()
    if (result?.available) {
      updateVersion.value = result.version || ""
      updateStatus.value = "available"
    } else {
      updateStatus.value = result && !result.error ? "current" : "failed"
    }
  } finally {
    updateBusy.value = false
  }
}

async function installUpdate() {
  updateBusy.value = true
  try {
    if (!(await installShellUpdate())) updateStatus.value = "failed"
  } finally {
    updateBusy.value = false
  }
}

async function loadSecrets() {
  try {
    secrets.value = await appSecretsAPI.status()
  } catch {
    secrets.value = { local: false, mode: "file", available: false, backend: "" }
  }
}

async function setKeychain(enabled) {
  keychainBusy.value = true
  keychainError.value = ""
  try {
    secrets.value = await appSecretsAPI.setMode(enabled ? "keychain" : "file")
  } catch (err) {
    keychainError.value = err.response?.data?.detail || t("settings.desktop.keychainFailed")
  } finally {
    keychainBusy.value = false
  }
}

async function loadShims() {
  try {
    shims.value = await appShimsAPI.status()
    available.value = true
  } catch {
    available.value = false
  }
}

async function loadAutostart() {
  hostReady.value = hasDesktopHost()
  const current = await getAutostart()
  if (current !== null) autostart.value = current
  const tray = await getCloseToTray()
  if (tray !== null) closeToTray.value = tray
  summonShortcut.value = await getSummonShortcut()
  const capabilities = await desktopCapabilities()
  explorerVerbs.value = capabilities?.explorerVerbs === true ? await getExplorerVerbs() : null
}

function stopRecording() {
  summonRecording.value = false
  window.removeEventListener("keydown", onRecordKey, true)
}

async function onRecordKey(event) {
  if (event.code === "Escape") {
    event.preventDefault()
    stopRecording()
    return
  }
  const accelerator = acceleratorFromEvent(event)
  if (!accelerator) return
  event.preventDefault()
  event.stopPropagation()
  stopRecording()
  if (await setSummonShortcut(accelerator)) {
    summonShortcut.value = accelerator
    summonError.value = false
  } else {
    summonError.value = true
  }
}

function recordSummon() {
  if (summonRecording.value) {
    stopRecording()
    return
  }
  summonError.value = false
  summonRecording.value = true
  window.addEventListener("keydown", onRecordKey, true)
}

async function clearSummon() {
  if (await setSummonShortcut(null)) summonShortcut.value = null
}

async function setTray(enabled) {
  const applied = await setCloseToTray(enabled)
  if (applied !== null) closeToTray.value = applied
}

async function setTerminal(enabled) {
  busy.value = true
  error.value = ""
  try {
    shims.value = enabled ? await appShimsAPI.install({ addToPath: true }) : await appShimsAPI.uninstall({ removeFromPath: true })
    justChanged.value = true
  } catch (err) {
    error.value = err.response?.data?.detail || t("settings.desktop.terminalFailed")
  } finally {
    busy.value = false
  }
}

async function setLogin(enabled) {
  const applied = await setAutostart(enabled)
  if (applied !== null) autostart.value = applied
}

let stopHostReady = () => {}

onMounted(() => {
  void loadShims()
  void loadSecrets()
  if (hasDesktopHost()) void loadRuntime()
  stopHostReady = onHostReady(() => void loadAutostart())
})

onBeforeUnmount(() => {
  stopHostReady()
  stopRecording()
  clearTimeout(repairTimer)
})
</script>

<style scoped>
.desktop-section-title {
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  opacity: 0.55;
  margin-top: 0.25rem;
}
</style>
