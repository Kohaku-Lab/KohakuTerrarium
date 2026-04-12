<template>
  <el-dialog
    v-model="open"
    :title="`${instance?.config_name || 'Instance'} Settings`"
    width="640px"
    :close-on-click-modal="true"
  >
    <div class="flex gap-0 h-96 -mx-4 -mb-4 overflow-hidden">
      <!-- Sidebar -->
      <div
        class="flex flex-col gap-0.5 py-2 px-1.5 border-r border-warm-200 dark:border-warm-700 shrink-0 w-32"
      >
        <button
          v-for="t in tabs"
          :key="t.id"
          class="flex items-center gap-2 px-2 py-1.5 rounded text-left text-[11px] transition-colors"
          :class="
            activeTab === t.id
              ? 'bg-iolite/10 text-iolite'
              : 'text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'
          "
          @click="activeTab = t.id"
        >
          <div :class="t.icon" class="text-[13px] shrink-0" />
          <span class="truncate">{{ t.label }}</span>
        </button>
      </div>

      <!-- Content -->
      <div class="flex-1 min-w-0 overflow-y-auto">
        <ModelTab v-if="activeTab === 'model'" :instance="instance" />
        <PluginsTab v-else-if="activeTab === 'plugins'" :instance="instance" />
        <ExtensionsTab v-else-if="activeTab === 'extensions'" />
        <TriggersTab v-else-if="activeTab === 'triggers'" :instance="instance" />
        <CostTab v-else-if="activeTab === 'cost'" :instance="instance" />
        <EnvTab v-else-if="activeTab === 'env'" :instance="instance" />
        <AutoOpenTab v-else-if="activeTab === 'auto-open'" />
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { ref } from "vue"

import AutoOpenTab from "@/components/panels/settings/AutoOpenTab.vue"
import CostTab from "@/components/panels/settings/CostTab.vue"
import EnvTab from "@/components/panels/settings/EnvTab.vue"
import ExtensionsTab from "@/components/panels/settings/ExtensionsTab.vue"
import ModelTab from "@/components/panels/settings/ModelTab.vue"
import PluginsTab from "@/components/panels/settings/PluginsTab.vue"
import TriggersTab from "@/components/panels/settings/TriggersTab.vue"

defineProps({ instance: { type: Object, default: null } })

const open = defineModel({ default: false })

const tabs = [
  { id: "model", label: "Model", icon: "i-carbon-chip" },
  { id: "plugins", label: "Plugins", icon: "i-carbon-plug" },
  { id: "extensions", label: "Extensions", icon: "i-carbon-cube" },
  { id: "triggers", label: "Triggers", icon: "i-carbon-event" },
  { id: "cost", label: "Cost", icon: "i-carbon-currency-dollar" },
  { id: "env", label: "Environment", icon: "i-carbon-cloud" },
  { id: "auto-open", label: "Auto-open", icon: "i-carbon-launch" },
]

const activeTab = ref("model")
</script>
