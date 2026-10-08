<template>
  <div class="mx-3 my-2 grid grid-cols-2 rounded-lg border border-solid border-warm-300 dark:border-warm-700 p-0.5 text-[12px]" role="radiogroup" :aria-label="t('lab.rail.app')" data-test="rail-app-switch">
    <button type="button" role="radio" :aria-checked="!inStudio" class="h-7 rounded-md flex items-center justify-center gap-1.5" :class="!inStudio ? ON : OFF" data-test="rail-app-terrarium" @click="tabs.openTab({ kind: 'dashboard', id: 'dashboard' })"><span class="i-carbon-network-4" />{{ t("lab.rail.terrarium") }}</button>
    <button type="button" role="radio" :aria-checked="inStudio" class="h-7 rounded-md flex items-center justify-center gap-1.5" :class="inStudio ? ON : OFF" data-test="rail-app-studio" @click="openStudio"><span class="i-carbon-tool-box" />{{ t("lab.rail.studio") }}</button>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useOpenStudio } from "@/components/shell/rail/useOpenStudio"
import { useTabsStore } from "@/stores/tabs"
import { useI18n } from "@/utils/i18n"

/** Terrarium (the lab and its sessions) or Studio (authoring): which app the active tab belongs to, and the way across. */
const ON = "bg-white dark:bg-warm-700 text-warm-800 dark:text-warm-100 shadow-sm font-medium"
const OFF = "text-warm-600 dark:text-warm-400 hover:text-warm-800 dark:hover:text-warm-200"

const { t } = useI18n()
const tabs = useTabsStore()
const openStudio = useOpenStudio()
const inStudio = computed(() => tabs.tabs.find((tab) => tab.id === tabs.activeId)?.kind === "studio-editor")
</script>
