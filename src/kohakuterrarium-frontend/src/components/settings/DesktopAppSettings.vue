<template>
  <SectionShell :title="t('settings.section.desktop')" :hint="t('settings.desc.desktop')" bare>
    <div v-if="showTerminal || showAutostart || showKeychain || showTray || showSummon || showUpdate || backend.status.value" id="desktop-app-settings" class="flex flex-col gap-6" data-desktop-app-settings>
      <SettingsGroup v-if="showTray || showRestore || showKeepAwake" :title="t('settings.desktop.sectionWindow')" data-section="window">
        <SettingsRow v-if="showTray" data-setting="close-to-tray" :title="t('settings.desktop.trayTitle')" :hint="t('settings.desktop.trayHint')" inline>
          <el-switch :model-value="closeToTray" @change="setTray" />
        </SettingsRow>
        <SettingsRow v-if="showRestore" data-setting="restore-windows" :title="t('settings.desktop.restoreTitle')" :hint="t('settings.desktop.restoreHint')" inline>
          <el-switch :model-value="shell.settings.value?.restorePopouts !== false" @change="(value) => shell.patch({ restorePopouts: value })" />
        </SettingsRow>
        <SettingsRow v-if="showKeepAwake" data-setting="keep-awake" :title="t('settings.desktop.keepAwakeTitle')" :hint="t('settings.desktop.keepAwakeHint')">
          <select class="kt-v2-edge kt-v2-panel h-8 px-2 rounded-lg border text-sm text-warm-700 dark:text-warm-200" data-keep-awake @change="(event) => shell.patch({ keepAwake: event.target.value })">
            <option value="never" :selected="shell.settings.value?.keepAwake === 'never'">{{ t("settings.desktop.keepAwakeNever") }}</option>
            <option value="running" :selected="shell.settings.value?.keepAwake === 'running'">{{ t("settings.desktop.keepAwakeRunning") }}</option>
            <option value="running-on-ac" :selected="shell.settings.value?.keepAwake === 'running-on-ac'">{{ t("settings.desktop.keepAwakeAc") }}</option>
          </select>
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup v-if="showAutostart" :title="t('settings.desktop.sectionStartup')" data-section="startup">
        <SettingsRow data-setting="autostart" :title="t('settings.desktop.autostartTitle')" :hint="t('settings.desktop.autostartHint')" inline>
          <el-switch :model-value="autostart" @change="setLogin" />
        </SettingsRow>
        <SettingsRow v-if="showRestore" data-setting="start-hidden" :title="t('settings.desktop.startHiddenTitle')" :hint="t('settings.desktop.startHiddenHint')" inline>
          <el-switch :model-value="shell.settings.value?.startHidden === true" :disabled="!autostart" @change="(value) => shell.patch({ startHidden: value })" />
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup v-if="showSummon" :title="t('settings.desktop.sectionShortcuts')" data-section="shortcuts">
        <SettingsRow data-setting="summon-shortcut" :title="t('settings.desktop.summonTitle')" :hint="t('settings.desktop.summonHint')">
          <template #detail>
            <div v-if="summonError" class="mt-1 text-[12px] text-coral" data-summon-error>{{ t("settings.desktop.summonUnavailable") }}</div>
          </template>
          <KButton size="sm" data-summon-record @click="recordSummon">{{ summonRecording ? t("settings.desktop.summonRecording") : summonShortcut ? displayAccelerator(summonShortcut) : t("settings.desktop.summonSet") }}</KButton>
          <KButton v-if="summonShortcut && !summonRecording" size="sm" variant="ghost" data-summon-clear @click="clearSummon">{{ t("settings.desktop.summonClear") }}</KButton>
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup v-if="showUpdate" :title="t('settings.desktop.sectionUpdates')" data-section="updates">
        <SettingsRow data-setting="shell-update" :title="t('settings.desktop.updateTitle')" :hint="t('settings.desktop.updateHint')">
          <template #detail>
            <div v-if="updateStatus === 'current'" class="mt-1 text-[12px] text-warm-500" data-update-status>{{ t("settings.desktop.updateCurrent") }}</div>
            <div v-else-if="updateStatus === 'available'" class="mt-1 text-[12px] text-iolite dark:text-iolite-light" data-update-status>{{ t("settings.desktop.updateAvailable", { version: updateVersion }) }}</div>
            <div v-else-if="updateStatus === 'failed'" class="mt-1 text-[12px] text-coral" data-update-status>{{ t("settings.desktop.updateFailed") }}</div>
          </template>
          <KButton v-if="updateStatus === 'available'" variant="primary" size="sm" data-update-install :disabled="updateBusy" @click="installUpdate">{{ t("settings.desktop.updateInstall") }}</KButton>
          <KButton v-else size="sm" data-update-check :disabled="updateBusy" @click="checkUpdate">{{ t("settings.desktop.updateCheck") }}</KButton>
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup v-if="backend.status.value" :title="t('settings.desktop.sectionBackend')" data-section="backend">
        <SettingsRow data-setting="backend" :title="t('settings.desktop.backendTitle')">
          <template #detail>
            <div class="mt-0.5 text-[12px] text-warm-500" data-backend-summary>{{ backendSummary }}</div>
          </template>
          <KButton size="sm" data-backend-logs @click="openLogs">{{ t("settings.desktop.backendLogs") }}</KButton>
          <KButton size="sm" data-backend-restart :disabled="backend.status.value.state === 'restarting'" @click="backend.restart">{{ t("settings.desktop.backendRestart") }}</KButton>
        </SettingsRow>
        <SettingsRow data-setting="runtime" :title="t('settings.desktop.runtimeTitle')">
          <template #detail>
            <div class="mt-0.5 text-[12px] text-warm-500" data-runtime-summary>{{ runtimeSummary }}</div>
            <div v-if="runtime?.bundled_newer" class="mt-1 text-[12px] text-amber-shadow dark:text-amber-light" data-runtime-newer>{{ t("settings.desktop.runtimeNewer", { version: runtime.bundled }) }}</div>
          </template>
          <KButton size="sm" :variant="repairArmed ? 'danger' : 'secondary'" data-runtime-repair @click="repair">{{ repairArmed ? t("settings.desktop.runtimeRepairConfirm") : t("settings.desktop.runtimeRepair") }}</KButton>
        </SettingsRow>
        <SettingsRow data-setting="diagnostics" :title="t('settings.desktop.diagnosticsTitle')" :hint="t('settings.desktop.diagnosticsHint')">
          <template #detail>
            <div v-if="diagnosticsResult?.ok" class="mt-1 text-[12px] text-aquamarine-shadow dark:text-aquamarine-light" data-diagnostics-result>{{ t("settings.desktop.diagnosticsSaved", { path: diagnosticsResult.path }) }}</div>
            <div v-else-if="diagnosticsResult" class="mt-1 text-[12px] text-coral" data-diagnostics-result>{{ t("settings.desktop.diagnosticsFailed") }}</div>
          </template>
          <KButton size="sm" :disabled="diagnosticsBusy" data-diagnostics-export @click="exportBundle">{{ t("settings.desktop.diagnosticsExport") }}</KButton>
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup v-if="explorerVerbs !== null || showTerminal || showKeychain" :title="t('settings.desktop.sectionIntegration')" data-section="integration">
        <SettingsRow v-if="explorerVerbs !== null" data-setting="explorer-verbs" :title="t('settings.desktop.explorerTitle')" :hint="t('settings.desktop.explorerHint')" inline>
          <el-switch :model-value="explorerVerbs" @change="setExplorer" />
        </SettingsRow>
        <SettingsRow v-if="showTerminal" data-setting="terminal-command" :title="t('settings.desktop.terminalTitle')" :hint="t('settings.desktop.terminalHint')" inline>
          <template #detail>
            <div v-if="shims.installed.length" class="mt-1 text-[12px] text-warm-500 font-mono break-all" data-terminal-location>{{ t("settings.desktop.terminalLocation", { dir: shims.target_dir }) }}</div>
            <div v-if="justChanged && terminalEnabled" class="mt-1 text-[12px] text-iolite dark:text-iolite-light" data-terminal-reopen>{{ t("settings.desktop.terminalReopen") }}</div>
            <div v-if="error" class="mt-1 text-[12px] text-coral" data-terminal-error>{{ error }}</div>
          </template>
          <el-switch :model-value="terminalEnabled" :loading="busy" @change="setTerminal" />
        </SettingsRow>
        <SettingsRow v-if="showKeychain" data-setting="keychain" :title="t('settings.desktop.keychainTitle')" :hint="t('settings.desktop.keychainHint', { backend: secrets.backend })" inline>
          <template #detail>
            <div v-if="keychainError" class="mt-1 text-[12px] text-coral" data-keychain-error>{{ keychainError }}</div>
          </template>
          <el-switch :model-value="secrets.mode === 'keychain'" :loading="keychainBusy" @change="setKeychain" />
        </SettingsRow>
      </SettingsGroup>
    </div>
  </SectionShell>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue"

import SectionShell from "@/components/session-v2/settings/SectionShell.vue"
import SettingsGroup from "@/components/settings/ui/SettingsGroup.vue"
import SettingsRow from "@/components/settings/ui/SettingsRow.vue"
import KButton from "@/components/common/ActionButton.vue"
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
