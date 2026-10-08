<template>
  <div class="flex flex-col gap-0.5 py-1">
    <button type="button" class="mx-3 mb-1.5 h-8 rounded-lg bg-iolite text-white hover:bg-iolite-shadow flex items-center justify-center gap-1.5 text-[13px] font-medium" data-test="rail-new-session" @click="newOpen = true"><span class="i-carbon-add-large" />{{ t("lab.new.title") }}</button>
    <button v-for="item in items" :key="item.id" type="button" class="flex items-center gap-2 px-3 py-1.5 kt-text-body text-left transition-colors" :class="isActive(item) ? 'text-warm-800 dark:text-warm-200 bg-warm-300/50 dark:bg-warm-700/50 font-medium' : 'text-warm-600 dark:text-warm-400 hover:bg-warm-300/50 dark:hover:bg-warm-700/50 hover:text-warm-800 dark:hover:text-warm-200'" :data-test="`rail-nav-${item.id}`" @click="go(item)">
      <span :class="item.icon" class="kt-text-body shrink-0" />
      <span>{{ item.label }}</span>
    </button>
    <NewSessionDialog v-if="newOpen" @close="newOpen = false" />
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import NewSessionDialog from "@/components/shell/newSession/NewSessionDialog.vue"
import { useAppMode } from "@/components/shell/rail/useAppMode"
import { useGoToApp } from "@/components/shell/rail/useOpenStudio"
import { useTabsStore } from "@/stores/tabs"
import { useI18n } from "@/utils/i18n"

/** The sidebar's way in: start a session, then the lab (back in Terrarium mode), history and library. */
const tabs = useTabsStore()
const { t } = useI18n()
const { appMode } = useAppMode()
const goTo = useGoToApp()
const newOpen = ref(false)

function go(item) {
  if (item.id === "lab") goTo("terrarium")
  else tabs.openTab(item.tab)
}

const items = computed(() => [
  { id: "lab", label: t("lab.rail.lab"), icon: "i-carbon-home", tab: { kind: "dashboard", id: "dashboard" } },
  { id: "history", label: t("lab.history.title"), icon: "i-carbon-recently-viewed", tab: { kind: "saved-sessions", id: "saved-sessions" } },
  { id: "library", label: t("lab.library.title"), icon: "i-carbon-catalog", tab: { kind: "catalog", id: "catalog" } },
])

function isActive(item) {
  return tabs.activeId === item.tab.id && (item.id !== "lab" || appMode.value === "terrarium")
}
</script>
