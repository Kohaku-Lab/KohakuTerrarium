<template>
  <div v-if="visible" class="fixed bottom-8 right-4 z-40 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-warm-200 dark:border-warm-700 bg-warm-50 dark:bg-warm-900 shadow-lg p-4 flex flex-col gap-3 text-xs" role="dialog" data-test="desktop-first-run">
    <div class="flex items-center gap-2 text-sm font-medium text-warm-800 dark:text-warm-200">
      <span class="i-carbon-screen text-iolite" />
      {{ t("desktop.firstRun.title") }}
    </div>
    <div class="text-warm-500 dark:text-warm-400">{{ t("desktop.firstRun.intro") }}</div>

    <div class="flex items-start justify-between gap-3" data-first-run="tray">
      <div>
        <div class="font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.trayTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-0.5">{{ t("settings.desktop.trayHint") }}</div>
      </div>
      <el-switch :model-value="settings.closeToTray === true" @change="(value) => shell.patch({ closeToTray: value })" />
    </div>

    <div v-if="autostart !== null" class="flex items-start justify-between gap-3" data-first-run="autostart">
      <div>
        <div class="font-medium text-warm-700 dark:text-warm-300">{{ t("settings.desktop.autostartTitle") }}</div>
        <div class="text-[11px] text-warm-400 mt-0.5">{{ t("settings.desktop.autostartHint") }}</div>
      </div>
      <el-switch :model-value="autostart" @change="setLogin" />
    </div>

    <div class="flex justify-between gap-2">
      <el-button size="small" data-first-run="open-settings" @click="openSettings">{{ t("desktop.firstRun.openSettings") }}</el-button>
      <el-button size="small" type="primary" data-first-run="done" @click="dismiss">{{ t("desktop.firstRun.done") }}</el-button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue"

import { useShellSettings } from "@/composables/useShellSettings"
import { openDesktopSettings } from "@/utils/desktopSettings"
import { getAutostart, setAutostart } from "@/utils/hostBridge"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const shell = useShellSettings()
const autostart = ref(null)

const settings = computed(() => shell.settings.value || {})
const visible = computed(() => shell.settings.value !== null && settings.value.firstRunDismissed !== true)

async function setLogin(enabled) {
  const applied = await setAutostart(enabled)
  if (applied !== null) autostart.value = applied
}

function dismiss() {
  void shell.patch({ firstRunDismissed: true })
}

function openSettings() {
  dismiss()
  openDesktopSettings()
}

onMounted(async () => {
  autostart.value = await getAutostart()
})
</script>
