<template>
  <div class="app-header flex items-center gap-2 px-3 h-8 border-b border-warm-200 dark:border-warm-700 bg-white dark:bg-warm-900 text-xs shrink-0">
    <StatusDot v-if="instance" :status="instance.status" />
    <span class="font-medium text-warm-700 dark:text-warm-300 truncate max-w-48">
      {{ instanceName }}
    </span>
    <GraphCounts v-if="instance" :instance="instance" compact />

    <button v-if="instance" class="w-5 h-5 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors" :title="t('appHeader.instanceSettings')" @click="settingsOpen = true">
      <div class="i-carbon-settings text-[11px]" />
    </button>

    <div class="seg-sep" />

    <el-dropdown trigger="click" size="small" @command="onPreset">
      <button class="flex items-center gap-1 px-1.5 py-0.5 rounded text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors">
        <span class="i-carbon-layout text-[12px] text-warm-400" />
        <span class="font-medium truncate max-w-32">{{ presetLabel }}</span>
        <span class="i-carbon-chevron-down text-[9px] opacity-50" />
      </button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item v-for="preset in presets" :key="preset.id" :command="preset.id" :disabled="layout.activePresetId === preset.id">
            <div class="flex items-center gap-2 text-[11px]">
              <span>{{ preset.localizedLabel }}</span>
              <span v-if="preset.shortcut" class="text-[9px] font-mono text-warm-400">{{ preset.shortcut }}</span>
            </div>
          </el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>

    <button class="w-6 h-6 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors" :title="t('appHeader.customizeLayout')" @click="fireLayoutEditRequested()">
      <div class="i-carbon-edit text-[11px]" />
    </button>

    <div class="flex-1" />

    <button class="flex items-center gap-1.5 px-2 py-0.5 rounded border border-warm-200 dark:border-warm-700 text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors" :title="t('appHeader.commandPalette')" @click="firePaletteOpen()">
      <span class="i-carbon-search text-[11px]" />
      <span class="text-[10px]">Ctrl+K</span>
    </button>

    <div class="seg-sep" />

    <button v-if="instance" class="w-6 h-6 flex items-center justify-center rounded text-warm-400 hover:text-coral transition-colors" :title="t('appHeader.stopInstance')" @click="$emit('stop')">
      <div class="i-carbon-stop-filled text-[11px]" />
    </button>
  </div>

  <InstanceSettingsModal v-if="instance" v-model="settingsOpen" :instance="instance" />
</template>

<script setup>
import { computed, ref } from "vue"

import InstanceSettingsModal from "@/components/chrome/InstanceSettingsModal.vue"
import { useInstanceContext } from "@/components/chrome/instanceContext"
import GraphCounts from "@/components/common/GraphCounts.vue"
import StatusDot from "@/components/common/StatusDot.vue"
import { useInstancesStore } from "@/stores/instances"
import { useLayoutStore } from "@/stores/layout"
import { useI18n } from "@/utils/i18n"
import { fireLayoutEditRequested, firePaletteOpen } from "@/utils/layoutEvents"

const props = defineProps({
  instanceId: { type: String, default: "" },
})

defineEmits(["stop"])

const settingsOpen = ref(false)

const route = useRoute()
const instances = useInstancesStore()
const layout = useLayoutStore()
const { t, presetLabel: translatePreset } = useI18n()

const { resolvedInstanceId, instance } = useInstanceContext(props, route, instances)

const instanceName = computed(() => instance.value?.config_name || instance.value?.creatures?.[0]?.name || resolvedInstanceId.value || "—")

const presetLabel = computed(() => {
  const preset = layout.activePreset
  if (!preset) return "—"
  return translatePreset(preset.id, preset.label || preset.id)
})

const PRESET_ORDER = ["chat-focus", "workspace", "multi-creature", "canvas", "debug", "chat-terminal"]

const presets = computed(() => {
  const all = layout.allPresets
  const output = []
  for (const id of PRESET_ORDER) {
    if (all[id]) output.push({ ...all[id], localizedLabel: translatePreset(all[id].id, all[id].label || all[id].id) })
  }
  for (const preset of Object.values(all)) {
    if (!PRESET_ORDER.includes(preset.id) && !preset.id.startsWith("legacy-")) {
      output.push({ ...preset, localizedLabel: translatePreset(preset.id, preset.label || preset.id) })
    }
  }
  return output
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
