<template>
  <div class="flex items-center gap-2 px-2 h-11 border-b border-warm-200 dark:border-warm-700 shrink-0 bg-white dark:bg-warm-900">
    <button class="w-8 h-8 flex items-center justify-center rounded text-warm-500 hover:text-iolite hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors" :title="t('shell.rail.commandPalette')" @click="$emit('open-rail')">
      <div class="i-carbon-menu text-base" />
    </button>
    <BrandMark v-if="!tabs.activeTab" class="w-6 h-6 rounded-full shrink-0" />
    <span class="text-sm font-medium text-warm-700 dark:text-warm-200 truncate flex-1">
      {{ title }}
    </span>
    <button class="w-8 h-8 flex items-center justify-center rounded text-warm-400 hover:text-iolite hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors" :title="t('density.useDesktopMode')" @click="forceDesktop">
      <div class="i-carbon-laptop text-base" />
    </button>
  </div>
</template>

<script setup>
import { computed } from "vue"

import BrandMark from "@/components/shell/BrandMark.vue"
import { useDensity } from "@/composables/useDensity"
import { useTabsStore } from "@/stores/tabs"
import { useI18n } from "@/utils/i18n"

defineEmits(["open-rail"])

const tabs = useTabsStore()
const { setOverride } = useDensity()
const { t } = useI18n()

const title = computed(() => tabs.activeTab?.label || "Kohaku Terrarium")

function forceDesktop() {
  // Pin to regular density so the user gets the multi-panel shell
  // even on a narrow viewport (the same affordance v1 mobile users
  // had via "Switch to desktop view").
  setOverride("regular")
}
</script>
