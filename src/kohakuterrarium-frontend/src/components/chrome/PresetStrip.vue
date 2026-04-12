<template>
  <!-- Compact strip: preset dropdown + edit/save buttons. ~24px total.
       NOT a row of tabs — doesn't eat vertical space. -->
  <div
    class="preset-strip flex items-center gap-1 px-2 h-6 border-b border-warm-200 dark:border-warm-700 bg-warm-50 dark:bg-warm-900 text-[10px]"
  >
    <div class="i-carbon-layout text-[12px] text-warm-400 mr-0.5" />

    <!-- Dropdown for the active preset (replaces old chip row) -->
    <el-dropdown trigger="click" @command="onSelect" size="small">
      <button
        class="flex items-center gap-1 px-1.5 py-0.5 rounded text-warm-700 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors"
      >
        <span class="font-medium truncate max-w-40">{{ activeLabel }}</span>
        <span class="i-carbon-chevron-down text-[9px] opacity-50" />
      </button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item
            v-for="p in presets"
            :key="p.id"
            :command="p.id"
            :disabled="active === p.id"
          >
            <div class="flex items-center gap-2 text-[11px]">
              <span class="truncate max-w-40">{{ p.label }}</span>
              <span v-if="p.shortcut" class="text-[9px] font-mono text-warm-400">{{
                p.shortcut
              }}</span>
            </div>
          </el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>

    <div class="flex-1" />

    <!-- Edit layout -->
    <button
      class="w-5 h-5 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors"
      title="Customize layout (Ctrl+Shift+L)"
      @click="onEdit"
    >
      <div class="i-carbon-edit text-[11px]" />
    </button>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useLayoutStore } from "@/stores/layout"
import { fireLayoutEditRequested } from "@/utils/layoutEvents"

const layout = useLayoutStore()

const PRESET_ORDER = ["chat-focus", "workspace", "multi-creature", "canvas", "debug", "settings"]

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

const active = computed(() => layout.activePresetId)
const activeLabel = computed(() => {
  const p = layout.activePreset
  return p?.label || p?.id || "—"
})

function onSelect(id) {
  layout.switchPreset(id)
}

function onEdit() {
  fireLayoutEditRequested()
}
</script>
