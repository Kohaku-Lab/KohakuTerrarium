<template>
  <div
    class="app-header flex items-center gap-2 px-3 h-8 border-b border-warm-200 dark:border-warm-700 bg-white dark:bg-warm-900 text-xs shrink-0"
  >
    <!-- Instance info -->
    <StatusDot v-if="instance" :status="instance.status" />
    <span class="font-medium text-warm-700 dark:text-warm-300 truncate max-w-48">
      {{ instance?.config_name || "—" }}
    </span>
    <span
      v-if="instance?.type"
      class="text-[9px] px-1.5 py-0.5 rounded bg-warm-100 dark:bg-warm-800 text-warm-400"
      >{{ instance.type }}</span
    >

    <!-- Instance settings gear -->
    <button
      v-if="instance"
      class="w-5 h-5 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors"
      title="Instance settings"
      @click="settingsOpen = true"
    >
      <div class="i-carbon-settings text-[11px]" />
    </button>

    <div class="seg-sep" />

    <!-- Preset dropdown -->
    <el-dropdown trigger="click" @command="onPreset" size="small">
      <button
        class="flex items-center gap-1 px-1.5 py-0.5 rounded text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors"
      >
        <span class="i-carbon-layout text-[12px] text-warm-400" />
        <span class="font-medium truncate max-w-32">{{ presetLabel }}</span>
        <span class="i-carbon-chevron-down text-[9px] opacity-50" />
      </button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item
            v-for="p in presets"
            :key="p.id"
            :command="p.id"
            :disabled="layout.activePresetId === p.id"
          >
            <div class="flex items-center gap-2 text-[11px]">
              <span>{{ p.label }}</span>
              <span v-if="p.shortcut" class="text-[9px] font-mono text-warm-400">{{
                p.shortcut
              }}</span>
            </div>
          </el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>

    <!-- Edit layout -->
    <button
      class="w-6 h-6 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors"
      title="Customize layout (Ctrl+Shift+L)"
      @click="fireLayoutEditRequested()"
    >
      <div class="i-carbon-edit text-[11px]" />
    </button>

    <!-- Spacer -->
    <div class="flex-1" />

    <!-- Cmd+K palette trigger -->
    <button
      class="flex items-center gap-1.5 px-2 py-0.5 rounded border border-warm-200 dark:border-warm-700 text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors"
      title="Command palette (Ctrl+K)"
      @click="firePaletteOpen()"
    >
      <span class="i-carbon-search text-[11px]" />
      <span class="text-[10px]">Ctrl+K</span>
    </button>

    <div class="seg-sep" />

    <!-- Stop instance -->
    <button
      v-if="instance"
      class="w-6 h-6 flex items-center justify-center rounded text-warm-400 hover:text-coral transition-colors"
      title="Stop instance"
      @click="$emit('stop')"
    >
      <div class="i-carbon-stop-filled text-[11px]" />
    </button>
  </div>

  <!-- Instance settings modal -->
  <InstanceSettingsModal v-if="instance" v-model="settingsOpen" :instance="instance" />
</template>

<script setup>
import { computed, ref } from "vue"

import InstanceSettingsModal from "@/components/chrome/InstanceSettingsModal.vue"
import StatusDot from "@/components/common/StatusDot.vue"
import { useInstancesStore } from "@/stores/instances"
import { useLayoutStore } from "@/stores/layout"
import { fireLayoutEditRequested, firePaletteOpen } from "@/utils/layoutEvents"

defineEmits(["stop"])

const settingsOpen = ref(false)

const instances = useInstancesStore()
const layout = useLayoutStore()

const instance = computed(() => instances.current)

const presetLabel = computed(() => {
  const p = layout.activePreset
  return p?.label || "—"
})

const PRESET_ORDER = [
  "chat-focus",
  "workspace",
  "multi-creature",
  "canvas",
  "debug",
  "chat-terminal",
]

const presets = computed(() => {
  const all = layout.allPresets
  const out = []
  for (const id of PRESET_ORDER) {
    if (all[id]) out.push(all[id])
  }
  for (const preset of Object.values(all)) {
    if (!PRESET_ORDER.includes(preset.id) && !preset.id.startsWith("legacy-")) {
      out.push(preset)
    }
  }
  return out
})

function onPreset(id) {
  layout.switchPreset(id)
}
</script>

<style scoped>
.seg-sep {
  width: 1px;
  height: 14px;
  background: currentColor;
  opacity: 0.12;
  flex-shrink: 0;
}
</style>
