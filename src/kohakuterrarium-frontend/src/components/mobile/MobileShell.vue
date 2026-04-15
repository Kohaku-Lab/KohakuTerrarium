<template>
  <div class="h-full flex flex-col overflow-hidden bg-warm-50 dark:bg-warm-950">
    <!-- Header -->
    <div class="flex items-center gap-2 px-2 h-11 border-b border-warm-200 dark:border-warm-700 shrink-0 bg-white dark:bg-warm-900">
      <MobileNav />
      <slot name="icon">
        <img src="/kohaku-icon.png" alt="" class="w-5 h-5 rounded-full object-cover" />
      </slot>
      <span class="text-sm font-medium text-warm-700 dark:text-warm-200 truncate flex-1">{{ title }}</span>
      <slot name="actions" />
      <!-- Switch to desktop button -->
      <button class="w-8 h-8 flex items-center justify-center rounded text-warm-400 hover:text-iolite transition-colors shrink-0" :title="t('common.desktopView')" @click="goDesktop">
        <div class="i-carbon-laptop text-base" />
      </button>
    </div>
    <!-- Body -->
    <div class="flex-1 min-h-0 overflow-hidden">
      <slot />
    </div>
    <!-- Optional bottom bar -->
    <slot name="bottom" />
  </div>
</template>

<script setup>
import { inject, provide } from "vue"

import MobileNav from "./MobileNav.vue"
import { useI18n } from "@/utils/i18n"

defineProps({
  title: { type: String, default: "KohakuTerrarium" },
})

provide("mobileLayout", true)
const { t } = useI18n()

const switchToDesktop = inject("switchToDesktop", null)

function goDesktop() {
  if (switchToDesktop) switchToDesktop()
}
</script>
