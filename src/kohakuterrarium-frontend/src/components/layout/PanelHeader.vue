<template>
  <div class="panel-header flex items-center gap-1 px-2 h-6 border-b border-warm-200/70 dark:border-warm-700/70 bg-warm-100/60 dark:bg-warm-900/60 text-[10px] text-warm-500 shrink-0">
    <div v-if="icon" :class="icon" class="text-[12px]" />
    <span class="font-medium truncate">{{ localizedLabel }}</span>

    <div class="flex-1" />

    <span v-if="layout.editMode && misplaced" class="text-amber text-[9px] flex items-center gap-0.5" :title="t('panelHeader.prefersTitle', { zone: preferredZoneLabel })">
      <span class="i-carbon-warning-alt text-[11px]" />
      <span>{{ t("panelHeader.prefers", { zone: preferredZoneLabel }) }}</span>
    </span>

    <el-dropdown v-if="layout.editMode" trigger="click" @command="onCommand">
      <button class="w-5 h-5 flex items-center justify-center rounded hover:bg-warm-200 dark:hover:bg-warm-800 text-warm-500" :title="t('panelHeader.menu')">
        <div class="i-carbon-overflow-menu-vertical text-[11px]" />
      </button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item command="replace">
            <div class="i-carbon-switcher mr-1" />
            {{ t("common.replace") }}
          </el-dropdown-item>
          <el-dropdown-item command="close" divided>
            <div class="i-carbon-close mr-1" />
            {{ t("common.close") }}
          </el-dropdown-item>
          <el-dropdown-item command="pop-out" :disabled="!panel?.supportsDetach">
            <div class="i-carbon-launch mr-1" />
            {{ t("panelHeader.popOut") }}
          </el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useLayoutStore } from "@/stores/layout"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  panelId: { type: String, required: true },
  zoneId: { type: String, required: true },
  instanceId: { type: String, default: "" },
})

const emit = defineEmits(["replace", "close", "pop-out"])

const layout = useLayoutStore()
const { panelLabel, t } = useI18n()

const panel = computed(() => layout.getPanel(props.panelId))
const localizedLabel = computed(() => panelLabel(props.panelId, panel.value?.label || props.panelId))
const icon = computed(() => {
  const map = {
    chat: "i-carbon-chat",
    "status-dashboard": "i-carbon-dashboard",
    files: "i-carbon-folder",
    "file-tree": "i-carbon-folder",
    activity: "i-carbon-pulse",
    state: "i-carbon-data-structured",
    creatures: "i-carbon-network-4",
    "monaco-editor": "i-carbon-code",
    "editor-status": "i-carbon-information",
    "status-bar": "i-carbon-information-square",
  }
  return map[props.panelId] || "i-carbon-panel-expansion"
})

const misplaced = computed(() => {
  const currentPanel = panel.value
  if (!currentPanel || !currentPanel.preferredZones || currentPanel.preferredZones.length === 0) return false
  return !currentPanel.preferredZones.includes(props.zoneId)
})

const preferredZoneLabel = computed(() => {
  const currentPanel = panel.value
  if (!currentPanel || !currentPanel.preferredZones?.length) return ""
  return currentPanel.preferredZones[0]
})

function onCommand(command) {
  if (command === "replace") emit("replace")
  else if (command === "close") emit("close")
  else if (command === "pop-out") emit("pop-out")
}
</script>
