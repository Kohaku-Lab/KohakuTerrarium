<template>
  <div class="h-full flex bg-warm-50 dark:bg-warm-900 overflow-hidden">
    <!-- Vertical tab strip on the left -->
    <div class="flex flex-col gap-0.5 py-2 px-1 border-r border-warm-200 dark:border-warm-700 shrink-0 min-w-24 w-auto">
      <button v-for="t in tabs" :key="t.id" class="flex items-center gap-2 px-2 py-1.5 rounded text-left text-[11px] transition-colors" :class="activeTab === t.id ? 'bg-iolite/10 text-iolite' : 'text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'" @click="activeTab = t.id">
        <div :class="t.icon" class="text-[13px] shrink-0" />
        <span class="truncate">{{ t.label }}</span>
      </button>
    </div>

    <!-- Body -->
    <div class="flex-1 min-w-0 overflow-hidden flex flex-col">
      <div class="flex items-center gap-2 px-4 py-2 border-b border-warm-200 dark:border-warm-700 shrink-0">
        <span class="text-xs font-medium text-warm-600 dark:text-warm-400">
          {{ activeLabel }}
        </span>
      </div>

      <div class="flex-1 overflow-y-auto">
        <ModelTab v-if="activeTab === 'model'" :instance="instance" />
        <WorkspaceTab v-else-if="activeTab === 'workspace'" :instance="instance" />
        <ModulesPanel v-else-if="activeTab === 'modules'" :instance="instance" />
        <ExtensionsTab v-else-if="activeTab === 'extensions'" />
        <TriggersTab v-else-if="activeTab === 'triggers'" :instance="instance" />
        <CostTab v-else-if="activeTab === 'cost'" :instance="instance" />
        <EnvTab v-else-if="activeTab === 'env'" :instance="instance" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import ModulesPanel from "./modules/ModulesPanel.vue"
import CostTab from "./settings/CostTab.vue"
import EnvTab from "./settings/EnvTab.vue"
import ExtensionsTab from "./settings/ExtensionsTab.vue"
import ModelTab from "./settings/ModelTab.vue"
import TriggersTab from "./settings/TriggersTab.vue"
import WorkspaceTab from "./settings/WorkspaceTab.vue"

defineProps({
  instance: { type: Object, default: null },
})

const tabs = [
  { id: "model", label: "Model", icon: "i-carbon-chip" },
  { id: "workspace", label: "Workspace", icon: "i-carbon-folder" },
  { id: "modules", label: "Modules", icon: "i-carbon-3d-mpr-toggle" },
  { id: "extensions", label: "Extensions", icon: "i-carbon-cube" },
  { id: "triggers", label: "Triggers", icon: "i-carbon-event" },
  { id: "cost", label: "Cost", icon: "i-carbon-currency-dollar" },
  { id: "env", label: "Environment", icon: "i-carbon-cloud" },
]

const activeTab = ref("model")

const activeLabel = computed(() => tabs.find((t) => t.id === activeTab.value)?.label || "")
</script>
