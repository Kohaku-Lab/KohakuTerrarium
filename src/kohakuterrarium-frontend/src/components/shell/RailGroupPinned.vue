<template>
  <div>
    <div class="flex items-center justify-between px-3 py-1">
      <span class="text-[10px] uppercase tracking-wider text-warm-500 font-medium"> {{ t("shell.rail.pinned") }} </span>
      <span class="text-[10px] text-warm-400">{{ pinnedTabs.length }}</span>
    </div>
    <div v-if="pinnedTabs.length === 0" class="px-3 py-2 text-[11px] text-warm-400 italic">{{ t("shell.rail.pinnedEmpty") }}</div>
    <div v-else class="flex flex-col gap-0.5">
      <button v-for="tab in pinnedTabs" :key="tab.id" class="flex items-center gap-2 px-3 py-1.5 text-sm text-warm-600 dark:text-warm-400 hover:bg-warm-300/50 dark:hover:bg-warm-700/50 cursor-pointer text-left" :class="tab.id === tabs.activeId ? 'text-warm-800 dark:text-warm-200 font-medium' : ''" @click="tabs.activateTab(tab.id)">
        <span :class="iconFor(tab.kind)" class="text-sm shrink-0" />
        <span class="truncate">{{ labelFor(tab) }}</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"
import { useTabsStore } from "@/stores/tabs"
import { useI18n } from "@/utils/i18n"

const tabs = useTabsStore()
const { t } = useI18n()

const pinnedTabs = computed(() => [...tabs.pinnedIds].map((id) => tabs.tabs.find((t) => t.id === id)).filter(Boolean))

function iconFor(kind) {
  return (
    {
      dashboard: "i-carbon-home",
      attach: "i-carbon-chat",
      inspector: "i-carbon-radar",
      "session-viewer": "i-carbon-recently-viewed",
      "studio-editor": "i-carbon-tool-box",
      catalog: "i-carbon-catalog",
      settings: "i-carbon-settings",
      "code-editor": "i-carbon-code",
    }[kind] ?? "i-carbon-circle"
  )
}

function labelFor(tab) {
  // Translate the well-known singletons through i18n; per-tab labels
  // (attach config_name, session name, etc.) stay raw because they
  // are user-data, not UI chrome.
  const localised = {
    dashboard: t("shell.rail.dashboard"),
    catalog: t("shell.quick.catalog"),
    settings: t("shell.quick.settings"),
  }[tab.kind]
  return localised ?? tab.config_name ?? tab.name ?? tab.entity ?? tab.slug ?? tab.id
}
</script>
