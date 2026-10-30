<template>
  <div v-if="showTerminal || showAutostart || showUpdate" class="border-t border-warm-200 dark:border-warm-700 pt-3 flex flex-col gap-3" data-desktop-app-settings>
    <div class="font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.title") }}</div>

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

    <div v-if="showAutostart" class="flex items-start justify-between gap-4" data-setting="autostart">
      <div>
        <div class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.autostartTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-1">{{ t("settings.desktop.autostartHint") }}</div>
      </div>
      <el-switch :model-value="autostart" @change="setLogin" />
    </div>

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
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue"

import { appShimsAPI } from "@/utils/appShimsApi"
import { checkShellUpdate, getAutostart, hasDesktopHost, hostSupports, installShellUpdate, onHostReady, setAutostart } from "@/utils/hostBridge"
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
  stopHostReady = onHostReady(() => void loadAutostart())
})

onBeforeUnmount(() => stopHostReady())
</script>
