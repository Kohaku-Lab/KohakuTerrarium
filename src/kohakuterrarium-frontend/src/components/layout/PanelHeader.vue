<template>
  <div
    class="panel-header flex items-center gap-1 px-2 h-6 border-b border-warm-200/70 dark:border-warm-700/70 bg-warm-100/60 dark:bg-warm-900/60 text-[10px] text-warm-500 shrink-0"
  >
    <div v-if="icon" :class="icon" class="text-[12px]" />
    <span class="font-medium truncate">{{ label || panelId }}</span>

    <div class="flex-1" />

    <!-- Orientation warning (edit mode only) -->
    <span
      v-if="layout.editMode && misplaced"
      class="text-amber text-[9px] flex items-center gap-0.5"
      :title="`Prefers ${preferredZoneLabel}`"
    >
      <span class="i-carbon-warning-alt text-[11px]" />
      <span>prefers {{ preferredZoneLabel }}</span>
    </span>

    <!-- Kebab menu, edit mode only -->
    <el-dropdown v-if="layout.editMode" trigger="click" @command="onCommand">
      <button
        class="w-5 h-5 flex items-center justify-center rounded hover:bg-warm-200 dark:hover:bg-warm-800 text-warm-500"
        title="Panel menu"
      >
        <div class="i-carbon-overflow-menu-vertical text-[11px]" />
      </button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item command="replace">
            <div class="i-carbon-switcher mr-1" />
            Replace…
          </el-dropdown-item>
          <el-dropdown-item command="close" divided>
            <div class="i-carbon-close mr-1" />
            Close
          </el-dropdown-item>
          <el-dropdown-item command="pop-out" :disabled="!panel?.supportsDetach">
            <div class="i-carbon-launch mr-1" />
            Pop out
          </el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useLayoutStore } from "@/stores/layout"

const props = defineProps({
  panelId: { type: String, required: true },
  zoneId: { type: String, required: true },
  instanceId: { type: String, default: "" },
})

const emit = defineEmits(["replace", "close", "pop-out"])

const layout = useLayoutStore()

const panel = computed(() => layout.getPanel(props.panelId))
const label = computed(() => panel.value?.label || props.panelId)
const icon = computed(() => {
  // Panel metadata doesn't yet carry icons — pick a sensible default.
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
  const p = panel.value
  if (!p || !p.preferredZones || p.preferredZones.length === 0) return false
  return !p.preferredZones.includes(props.zoneId)
})

const preferredZoneLabel = computed(() => {
  const p = panel.value
  if (!p || !p.preferredZones?.length) return ""
  return p.preferredZones[0]
})

function onCommand(cmd) {
  if (cmd === "replace") emit("replace")
  else if (cmd === "close") emit("close")
  else if (cmd === "pop-out") emit("pop-out")
}
</script>
