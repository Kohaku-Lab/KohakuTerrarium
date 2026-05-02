<template>
  <div class="flex flex-col gap-0.5">
    <button class="flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer text-left transition-colors" :class="isActive('dashboard') ? 'text-warm-800 dark:text-warm-200 bg-warm-300/50 dark:bg-warm-700/50 font-medium' : 'text-warm-600 dark:text-warm-400 hover:bg-warm-300/50 dark:hover:bg-warm-700/50 hover:text-warm-800 dark:hover:text-warm-200'" @click="openDashboard">
      <span class="i-carbon-home text-sm shrink-0" />
      <span>{{ t("shell.rail.dashboard") }}</span>
    </button>
  </div>
</template>

<script setup>
import { useTabsStore } from "@/stores/tabs"
import { useI18n } from "@/utils/i18n"

const tabs = useTabsStore()
const { t } = useI18n()

function isActive(id) {
  return tabs.activeId === id
}

function openDashboard() {
  tabs.openTab({ kind: "dashboard", id: "dashboard" })
}
</script>
