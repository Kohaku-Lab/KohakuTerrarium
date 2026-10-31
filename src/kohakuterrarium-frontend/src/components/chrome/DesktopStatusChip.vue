<template>
  <button v-if="visible" class="status-seg flex items-center gap-1 shrink-0 hover:text-warm-700 dark:hover:text-warm-300 transition-colors" :title="tooltip" data-test="desktop-chip" @click="openDesktopSettings">
    <span :class="keepingAwake ? 'i-carbon-sun' : 'i-carbon-screen'" class="text-[11px]" />
    <span>{{ t("desktop.chip.label") }}</span>
    <span v-if="!settings.firstRunDismissed" class="w-1.5 h-1.5 rounded-full bg-iolite" data-test="desktop-chip-dot" />
  </button>
</template>

<script setup>
import { computed } from "vue"

import { keepingAwake } from "@/composables/useKeepAwake"
import { useShellSettings } from "@/composables/useShellSettings"
import { displayAccelerator } from "@/utils/accelerator"
import { openDesktopSettings } from "@/utils/desktopSettings"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const shell = useShellSettings()

const settings = computed(() => shell.settings.value || {})
const visible = computed(() => shell.settings.value !== null)

const tooltip = computed(() => {
  const state = (on) => t(on ? "desktop.chip.on" : "desktop.chip.off")
  const shortcut = settings.value.summonShortcut
  const lines = [t("desktop.chip.tooltip"), `${t("settings.desktop.trayTitle")}: ${state(settings.value.closeToTray)}`, `${t("settings.desktop.restoreTitle")}: ${state(settings.value.restorePopouts !== false)}`, `${t("settings.desktop.summonTitle")}: ${shortcut ? displayAccelerator(shortcut) : t("desktop.chip.notSet")}`]
  if (keepingAwake.value) lines.push(t("desktop.chip.awake"))
  return lines.join("\n")
})
</script>
