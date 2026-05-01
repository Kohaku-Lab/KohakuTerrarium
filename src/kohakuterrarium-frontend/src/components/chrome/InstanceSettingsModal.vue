<template>
  <el-dialog v-model="open" :title="t('instanceSettings.title', { name: instance?.config_name || t('common.terrarium') })" width="640px" :close-on-click-modal="true">
    <div class="flex gap-0 h-96 -mx-4 -mb-4 overflow-hidden">
      <div class="flex flex-col gap-0.5 py-2 px-1.5 border-r border-warm-200 dark:border-warm-700 shrink-0 w-32">
        <button v-for="tab in tabs" :key="tab.id" class="flex items-center gap-2 px-2 py-1.5 rounded text-left text-[11px] transition-colors" :class="activeTab === tab.id ? 'bg-iolite/10 text-iolite' : 'text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'" @click="activeTab = tab.id">
          <div :class="tab.icon" class="text-[13px] shrink-0" />
          <span class="truncate">{{ tab.label }}</span>
        </button>
      </div>

      <div class="flex-1 min-w-0 overflow-y-auto">
        <ModelTab v-if="activeTab === 'model'" :instance="instance" />
        <WorkspaceTab v-else-if="activeTab === 'workspace'" :instance="instance" />
        <ModulesPanel v-else-if="activeTab === 'modules'" :instance="instance" />
        <ExtensionsTab v-else-if="activeTab === 'extensions'" />
        <TriggersTab v-else-if="activeTab === 'triggers'" :instance="instance" />
        <CostTab v-else-if="activeTab === 'cost'" :instance="instance" />
        <EnvTab v-else-if="activeTab === 'env'" :instance="instance" />
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, ref } from "vue"

import ModulesPanel from "@/components/panels/modules/ModulesPanel.vue"
import CostTab from "@/components/panels/settings/CostTab.vue"
import EnvTab from "@/components/panels/settings/EnvTab.vue"
import ExtensionsTab from "@/components/panels/settings/ExtensionsTab.vue"
import ModelTab from "@/components/panels/settings/ModelTab.vue"
import TriggersTab from "@/components/panels/settings/TriggersTab.vue"
import WorkspaceTab from "@/components/panels/settings/WorkspaceTab.vue"
import { useI18n } from "@/utils/i18n"

defineProps({ instance: { type: Object, default: null } })

const open = defineModel({ default: false })
const { t } = useI18n()

const tabs = computed(() => [
  { id: "model", label: t("instanceSettings.model"), icon: "i-carbon-chip" },
  { id: "workspace", label: "Workspace", icon: "i-carbon-folder" },
  { id: "modules", label: "Modules", icon: "i-carbon-3d-mpr-toggle" },
  { id: "extensions", label: t("instanceSettings.extensions"), icon: "i-carbon-cube" },
  { id: "triggers", label: t("instanceSettings.triggers"), icon: "i-carbon-event" },
  { id: "cost", label: t("instanceSettings.cost"), icon: "i-carbon-currency-dollar" },
  { id: "env", label: t("instanceSettings.environment"), icon: "i-carbon-cloud" },
])

const activeTab = ref("model")
</script>
